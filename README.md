# filepile

Консольный проводник, в котором папка — это куча хлама. Папки — коробки, картинки — картины в рамах, архивы — мешки, видео — телики, логи — брёвна, неведомое — покрышки. Чем больше файл, тем крупнее предмет.

```bash
git clone https://github.com/Baklatech/filepile && cd filepile && ./install.sh
filepile ~/Downloads
```

На Omarchy открывается по `Super+Ctrl+Shift+F`.

`←↑↓→` или щелчок — выбрать, `enter` или двойной щелчок — открыть, `⌫` наверх, `.` скрытые, `/` поиск, `e` редактор, `s` встряхнуть, `q` выход.

Чтобы после выхода оказаться в той папке, где остановился:

```bash
fp() { local f; f=$(mktemp); filepile --cwd-file "$f" "$@"; cd "$(cat "$f")"; rm -f "$f"; }
```

Навайбкожено в [Baklatech](https://github.com/Baklatech) · GPL-3.0
