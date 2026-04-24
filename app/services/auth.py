from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException, status
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_signed_state, verify_signed_state
from app.core.settings import Settings
from app.models.oauth_account import OAuthAccount
from app.models.user import User


GOOGLE_PROVIDER = "google"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
OAUTH_STATE_COOKIE_NAME = "ytpipe_oauth_nonce"
OAUTH_PKCE_COOKIE_NAME = "ytpipe_oauth_pkce_verifier"


@dataclass
class OAuthResult:
    user: User
    oauth_account: OAuthAccount


class AuthFlowError(Exception):
    pass


class AuthConfigurationError(AuthFlowError):
    pass


class AuthStateError(AuthFlowError):
    pass


class SingleUserViolation(AuthFlowError):
    pass


class GoogleOAuthService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def create_authorization_url(self) -> tuple[str, str, str]:
        self._ensure_google_oauth_is_configured()
        state, nonce = create_signed_state(self.settings.app_secret_key)
        flow = self._create_flow(state=state)
        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        code_verifier = flow.code_verifier
        if not code_verifier:
            raise AuthFlowError("Google OAuth did not generate a PKCE code verifier.")
        return authorization_url, nonce, code_verifier

    def exchange_code_for_account(
        self,
        session: Session,
        code: str,
        state: str,
        cookie_nonce: str | None,
        cookie_code_verifier: str | None,
    ) -> OAuthResult:
        self._ensure_google_oauth_is_configured()
        try:
            payload = verify_signed_state(state, self.settings.app_secret_key)
        except ValueError as exc:
            raise AuthStateError(str(exc)) from exc

        if not cookie_nonce or payload.get("nonce") != cookie_nonce:
            raise AuthStateError("OAuth state does not match the initiating browser session.")

        if not cookie_code_verifier:
            raise AuthStateError("OAuth PKCE code verifier is missing from the initiating browser session.")

        flow = self._create_flow(state=state)
        flow.code_verifier = cookie_code_verifier
        try:
            flow.fetch_token(code=code)
        except Exception as exc:
            error_message = "Google OAuth code exchange failed."
            if self.settings.app_env == "local":
                error_message = f"Google OAuth code exchange failed: {exc}"
            raise AuthFlowError(error_message) from exc

        credentials = flow.credentials
        try:
            email = self._fetch_user_email(credentials.token)
        except httpx.HTTPError as exc:
            raise AuthFlowError("Google user profile lookup failed.") from exc
        user = self._get_or_create_single_user(session, email)
        oauth_account = self._upsert_oauth_account(session, user, credentials)
        session.flush()
        return OAuthResult(user=user, oauth_account=oauth_account)

    def ensure_valid_credentials(self, session: Session, oauth_account: OAuthAccount) -> Credentials:
        self._ensure_google_oauth_is_configured()
        credentials = Credentials(
            token=oauth_account.access_token,
            refresh_token=oauth_account.refresh_token,
            token_uri=self.settings.google_client_config["web"]["token_uri"],
            client_id=self.settings.google_client_id,
            client_secret=self.settings.google_client_secret,
            scopes=list(self.settings.google_oauth_scopes),
        )
        credentials.expiry = self._normalize_google_credentials_expiry(oauth_account.token_expiry)

        if credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Stored Google credentials can no longer be refreshed. Manual re-auth is required.",
                ) from exc
            oauth_account.access_token = credentials.token
            if credentials.refresh_token:
                oauth_account.refresh_token = credentials.refresh_token
            oauth_account.token_expiry = self._normalize_utc_datetime(credentials.expiry)
            session.flush()

        if not credentials.valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Stored Google credentials are not valid. Manual re-auth is required.",
            )

        return credentials

    def _create_flow(self, state: str | None = None) -> Flow:
        flow = Flow.from_client_config(
            client_config=self.settings.google_client_config,
            scopes=list(self.settings.google_oauth_scopes),
            state=state,
        )
        flow.redirect_uri = self.settings.google_redirect_uri
        return flow

    def _fetch_user_email(self, access_token: str) -> str:
        response = httpx.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
        email = payload.get("email")
        email_verified = payload.get("email_verified")
        if not isinstance(email, str) or not email:
            raise AuthFlowError("Google userinfo response did not include an email address.")
        if email_verified is not True:
            raise AuthFlowError("Google account email must be verified before login is accepted.")
        return email

    def _get_or_create_single_user(self, session: Session, email: str) -> User:
        users = session.scalars(select(User)).all()
        if len(users) > 1:
            raise SingleUserViolation("This MVP expects exactly one local user record.")
        if not users:
            user = User(email=email)
            session.add(user)
            session.flush()
            return user

        user = users[0]
        if user.email != email:
            raise SingleUserViolation(
                "This MVP supports only one Google account. Re-authenticate with the original account.",
            )
        return user

    def _upsert_oauth_account(self, session: Session, user: User, credentials: Credentials) -> OAuthAccount:
        oauth_account = session.scalar(
            select(OAuthAccount).where(
                OAuthAccount.user_id == user.id,
                OAuthAccount.provider == GOOGLE_PROVIDER,
            )
        )

        refresh_token = credentials.refresh_token
        if oauth_account is None:
            if not refresh_token:
                raise AuthFlowError("Google OAuth did not return a refresh token.")
            oauth_account = OAuthAccount(user_id=user.id, provider=GOOGLE_PROVIDER, access_token="", refresh_token="")
            session.add(oauth_account)

        oauth_account.access_token = credentials.token
        oauth_account.refresh_token = refresh_token or oauth_account.refresh_token
        oauth_account.token_expiry = self._normalize_utc_datetime(credentials.expiry)
        return oauth_account

    @staticmethod
    def _normalize_utc_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _normalize_google_credentials_expiry(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def _ensure_google_oauth_is_configured(self) -> None:
        if not self.settings.google_client_id or not self.settings.google_client_secret:
            raise AuthConfigurationError("Google OAuth credentials are not configured.")
        if not self.settings.app_secret_key or self.settings.app_secret_key == "replace-me":
            raise AuthConfigurationError("APP_SECRET_KEY must be configured for OAuth state signing.")
