#!/bin/bash
# Двойной клик в Finder: расшифровать экспорт настроек AAPS в data/raw/settings.json.
cd "$(dirname "$0")/.." || exit 1
EXPORT="$HOME/Downloads/2026-11-10_125705_full.json"
if [ ! -f "$EXPORT" ]; then
  echo "Не найден $EXPORT"
  read -r -p "Путь к экспорту: " EXPORT
fi
python3 tools/decrypt_prefs.py "$EXPORT" -o data/raw/settings.json
echo
read -r -p "Готово. Enter — закрыть окно."
