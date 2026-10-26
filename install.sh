#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Ставит filepile в систему — одинаково на Omarchy, Arch и Debian-based:
# копирует программу, ярлык для лаунчера и иконку. На Omarchy ещё добавляет
# пункт в меню, сочетание SUPER+CTRL+SHIFT+F и предлагает сделать окно
# непрозрачным.
#
#   ./install.sh              в /usr/local (спросит sudo)
#   ./install.sh --user       в ~/.local, без sudo
#   ./install.sh --opaque     непрозрачное окно, не спрашивая (--no-opaque — убрать)
#   ./install.sh --no-hotkey  без сочетания клавиш (и убрать, если было)
#   ./install.sh --uninstall  удалить (вместе с --user — из ~/.local)
set -euo pipefail
cd "$(dirname "$0")"

PREFIX=/usr/local
SUDO=sudo
ACTION=install
OPAQUE=ask
HOTKEY=yes
for arg in "$@"; do
  case $arg in
    --user) PREFIX=$HOME/.local SUDO= ;;
    --uninstall) ACTION=uninstall ;;
    --opaque) OPAQUE=yes ;;
    --no-opaque) OPAQUE=no ;;
    --no-hotkey) HOTKEY=no ;;
    *) echo "usage: $0 [--user] [--opaque|--no-opaque] [--no-hotkey] [--uninstall]" >&2; exit 2 ;;
  esac
done
(( EUID == 0 )) && SUDO=

BIN=$PREFIX/bin/filepile
DESKTOP=$PREFIX/share/applications/filepile.desktop
ICON=$PREFIX/share/icons/hicolor/scalable/apps/filepile.svg
MENU=${OMARCHY_MENU_FILE:-$HOME/.config/omarchy/extensions/omarchy-menu.jsonc}
MENU_DEFAULT=/usr/share/omarchy/config/omarchy/extensions/omarchy-menu.jsonc
HYPR=${HYPR_CONFIG_FILE:-$HOME/.config/hypr/hyprland.lua}
BINDINGS=${HYPR_BINDINGS_FILE:-$HOME/.config/hypr/bindings.lua}

is_omarchy() { command -v omarchy-menu >/dev/null || [[ -d $HOME/.config/omarchy ]]; }

# Пункт меню лежит между маркерами, поэтому его можно обновлять и убирать,
# не задевая остальное содержимое файла.
backup() { cp "$1" "$1.bak.$(date +%s)"; }

menu_strip() { sed -i '/^\/\/ >>> filepile >>>$/,/^\/\/ <<< filepile <<<$/d' "$MENU"; }

menu_remove() {
  [[ -f $MENU ]] && grep -q '^// >>> filepile >>>$' "$MENU" || return 0
  backup "$MENU"
  menu_strip
}

menu_add() {
  if [[ -f $MENU ]]; then
    backup "$MENU"
    menu_strip
  else
    mkdir -p "$(dirname "$MENU")"
    if [[ -f $MENU_DEFAULT ]]; then cp "$MENU_DEFAULT" "$MENU"; else printf '{\n}\n' >"$MENU"; fi
  fi
  if [[ $(head -n1 "$MENU") != "{" ]]; then
    echo "$MENU: первая строка не «{», пункт меню не добавлен" >&2
    return 0
  fi
  local icon entry
  icon=$(printf '\U000F024B')   # nf-md-folder
  entry="  \"filepile\": {\"icon\": \"$icon\", \"label\": \"Filepile\", \"aliases\": [\"filepile\", \"files\", \"проводник\"], \"description\": \"filepile — проводник-куча хлама\", \"action\": \"omarchy-launch-or-focus-tui $BIN\"},"
  sed -i "1a // >>> filepile >>>\n$entry\n// <<< filepile <<<" "$MENU"
  echo "пункт Filepile добавлен в меню Omarchy"
}

# Omarchy делает все окна слегка прозрачными на уровне композитора — из самой
# программы это не отключить, только правилом окна. Правило тоже лежит между
# маркерами. Класс окна — тот, что даёт omarchy-launch-or-focus-tui.
opaque_has() { [[ -f $HYPR ]] && grep -q '^-- >>> filepile >>>$' "$HYPR"; }

opaque_strip() { sed -i '/^-- >>> filepile >>>$/,/^-- <<< filepile <<<$/d' "$HYPR"; }

opaque_remove() {
  opaque_has || return 0
  backup "$HYPR"
  opaque_strip
  echo "правило непрозрачности убрано из $HYPR"
}

opaque_add() {
  opaque_has && return 0
  backup "$HYPR"
  [[ -z $(tail -c1 "$HYPR") ]] || echo >>"$HYPR"
  cat >>"$HYPR" <<'EOF'
-- >>> filepile >>>
o.window("org.omarchy.filepile", { tag = "-default-opacity", opacity = "1 1" })
-- <<< filepile <<<
EOF
  echo "окно filepile теперь непрозрачное (правило в $HYPR)"
}

opaque_setup() {
  # правило написано под Lua-конфиг Omarchy; в других местах ему делать нечего
  [[ -f $HYPR ]] && grep -q 'default\.hypr' "$HYPR" || return 0
  local want=$OPAQUE reply def=n
  if [[ $want == ask ]]; then
    [[ -t 0 ]] || return 0      # без терминала не спрашиваем и ничего не меняем
    if opaque_has; then def=y; fi
    read -rp "Сделать окно filepile непрозрачным (правило в hyprland.lua)? [$([[ $def == y ]] && echo Y/n || echo y/N)] " reply
    case ${reply:-$def} in
      [yYдД]*) want=yes ;;
      *) want=no ;;
    esac
  fi
  if [[ $want == yes ]]; then opaque_add; else opaque_remove; fi
}

# Сочетание выбрано так, чтобы не пересекаться со стандартными в Omarchy
# (рядом SUPER+SHIFT+F — файловый менеджер). Если его занял кто-то ещё,
# ничего не перебиваем. Строка тоже лежит между маркерами.
hotkey_has() { [[ -f $BINDINGS ]] && grep -q '^-- >>> filepile >>>$' "$BINDINGS"; }

hotkey_strip() { sed -i '/^-- >>> filepile >>>$/,/^-- <<< filepile <<<$/d' "$BINDINGS"; }

hotkey_remove() {
  hotkey_has || return 0
  backup "$BINDINGS"
  hotkey_strip
  echo "сочетание SUPER+CTRL+SHIFT+F убрано из $BINDINGS"
}

# занято ли сочетание чем-то, кроме самого filepile (SUPER=64 + CTRL=4 + SHIFT=1)
hotkey_busy() {
  command -v hyprctl >/dev/null || return 1
  hyprctl binds -j 2>/dev/null | python3 -c '
import json, sys
sys.exit(0 if any(b["modmask"] == 69 and b["key"].upper() == "F" and "filepile" not in b["arg"]
                  for b in json.load(sys.stdin)) else 1)'
}

hotkey_add() {
  # строка написана под Lua-конфиг Omarchy; в других местах ей делать нечего
  [[ -f $BINDINGS ]] || return 0
  if hotkey_busy; then
    echo "SUPER+CTRL+SHIFT+F уже чем-то занято — сочетание для filepile не добавлено" >&2
    return 0
  fi
  backup "$BINDINGS"
  hotkey_has && hotkey_strip
  [[ -z $(tail -c1 "$BINDINGS") ]] || echo >>"$BINDINGS"
  printf '%s\n' '-- >>> filepile >>>' \
    "o.bind(\"SUPER + CTRL + SHIFT + F\", \"Filepile\", { tui = \"$BIN\", focus = true })" \
    '-- <<< filepile <<<' >>"$BINDINGS"
  echo "filepile открывается по SUPER+CTRL+SHIFT+F (строка в $BINDINGS)"
}

refresh_caches() {
  if command -v gtk-update-icon-cache >/dev/null; then
    $SUDO gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true
  fi
  if command -v update-desktop-database >/dev/null; then
    $SUDO update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
  fi
}

if [[ $ACTION == uninstall ]]; then
  $SUDO rm -f "$BIN" "$DESKTOP" "$ICON"
  refresh_caches
  if is_omarchy; then menu_remove; opaque_remove; hotkey_remove; fi
  echo "filepile удалён из $PREFIX"
  exit 0
fi

if ! command -v python3 >/dev/null; then
  if command -v pacman >/dev/null; then hint="sudo pacman -S python"; else hint="sudo apt install python3"; fi
  echo "нужен python3: $hint" >&2
  exit 1
fi

# в ярлыке — полный путь: у лаунчера в PATH может не быть $PREFIX/bin
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
sed "s|^Exec=.*|Exec=$BIN|" packaging/filepile.desktop >"$tmp"

$SUDO install -Dm755 filepile.py "$BIN"
$SUDO install -Dm644 "$tmp" "$DESKTOP"
$SUDO install -Dm644 packaging/filepile.svg "$ICON"
refresh_caches
if is_omarchy; then
  menu_add
  if [[ $HOTKEY == yes ]]; then hotkey_add; else hotkey_remove; fi
  opaque_setup
fi

echo "готово: $BIN"
case ":$PATH:" in
  *":$PREFIX/bin:"*) ;;
  *) echo "внимание: $PREFIX/bin нет в PATH — запускай по полному пути или добавь его" ;;
esac
