from __future__ import annotations

import re

BROKER_VALIDATION_ERROR = "Broker returned invalid summary output."


def validate_broker_output(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(BROKER_VALIDATION_ERROR)
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    if not normalized or any(tag in normalized.casefold() for tag in ("<think", "</think>")):
        raise ValueError(BROKER_VALIDATION_ERROR)
    for line in lines:
        stripped = line.lstrip()
        if re.match(r"(?:#|```|~~~)", stripped) or re.match(r"1[.)] ", stripped):
            raise ValueError(BROKER_VALIDATION_ERROR)
    headings = ["RESUMEN", "PUNTOS CLAVE", "CONCLUSIÓN"]
    if any(lines.count(h) != 1 for h in headings):
        raise ValueError(BROKER_VALIDATION_ERROR)
    positions = [lines.index(h) for h in headings]
    if positions != sorted(positions) or any(lines[i].strip() != lines[i] for i in positions):
        raise ValueError(BROKER_VALIDATION_ERROR)
    if any(re.fullmatch(r"[A-ZÁÉÍÓÚÜÑ ]+", line or "") and line not in headings for line in lines):
        raise ValueError(BROKER_VALIDATION_ERROR)
    resumen = lines[positions[0] + 1:positions[1]]
    points = lines[positions[1] + 1:positions[2]]
    conclusion = lines[positions[2] + 1:]
    if not any(line.strip() for line in resumen) or not any(line.strip() for line in conclusion):
        raise ValueError(BROKER_VALIDATION_ERROR)
    bullets = [line for line in points if line.strip()]
    if not 4 <= len(bullets) <= 7 or any(not line.startswith("•") or not line[1:].strip() for line in bullets):
        raise ValueError(BROKER_VALIDATION_ERROR)
    if any(line.strip() and not line.startswith("•") for line in points):
        raise ValueError(BROKER_VALIDATION_ERROR)
    return "\n".join(lines)
