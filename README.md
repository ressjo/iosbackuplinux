# iOS Backup für Linux

Grafische App (Qt/KDE) für **Arch Linux / CachyOS**, um iPhone und iPad per USB zu
sichern und wiederherzustellen – ähnlich wie früher in iTunes. Der Speicherort ist
frei wählbar, z. B. ein eingehängtes NAS.

Die eigentliche Kommunikation mit dem Gerät übernimmt
[libimobiledevice](https://libimobiledevice.org/) (`idevicebackup2`); die Backups haben
das gleiche Format wie bei iTunes/Finder.

## Funktionen

- Angeschlossene iPhones/iPads werden automatisch erkannt (Name, Modell, iOS, Speicher)
- Kopplung („Diesem Computer vertrauen?“) direkt aus der App
- **Backup** mit Fortschrittsanzeige; Folge-Backups übertragen nur Änderungen
- **Wiederherstellen** – auch ein Backup eines anderen Geräts auf ein neues Gerät
- **Verschlüsselte Backups** einschalten/ausschalten, Passwort ändern
  (Passwörter werden nicht gespeichert und nicht als Kommandozeilenargument übergeben)
- **Backup-Verwaltung**: Liste aller Backups im Speicherort mit Datum, Größe,
  Verschlüsselung und Status; Ordner öffnen, löschen
- **NAS-Schutz**: Backups werden verweigert, wenn das NAS nicht eingehängt ist, damit
  nicht versehentlich in den leeren Mount-Ordner auf der lokalen Platte geschrieben wird

## Installation (CachyOS / Arch)

```bash
git clone https://github.com/ressjo/iosbackuplinux.git
cd iosbackuplinux/packaging/arch
makepkg -si
```

Das installiert die Abhängigkeiten (`python`, `pyside6`, `libimobiledevice`, `usbmuxd`),
die App und einen Startmenü-Eintrag „iOS Backup“.

Ohne Paket direkt aus dem Quellordner starten:

```bash
sudo pacman -S --needed python pyside6 libimobiledevice usbmuxd
PYTHONPATH=src python -m iosbackup
```

`usbmuxd` wird beim Anstecken eines Geräts automatisch per udev gestartet. Falls die App
meldet, dass usbmuxd nicht läuft:

```bash
sudo systemctl start usbmuxd
```

## Benutzung

1. iPhone/iPad per USB anschließen und entsperren.
2. Auf dem Gerät „Diesem Computer vertrauen?“ → **Vertrauen** (ggf. Code eingeben).
3. Unten unter **Speicherort** auf „Ändern …“ klicken und den NAS-Ordner wählen.
4. **Jetzt sichern** klicken.

Zum **Wiederherstellen** auf dem Zielgerät vorher **„Wo ist?“ ausschalten**
(Einstellungen → [Name] → Wo ist? → Mein iPhone suchen). Das Gerät startet danach neu.

Backups liegen als `<Speicherort>/<UDID>/` vor. Bei verschlüsselten Backups ohne
Passwort ist keine Wiederherstellung möglich – Passwort gut aufbewahren.

## NAS einbinden

Die App braucht einen **echten eingehängten Ordner**. Die `smb://`-Adressen in Dolphin
(KIO) sind kein Ordner im Dateisystem und funktionieren deshalb nicht.

Empfohlen: SMB-Freigabe per `/etc/fstab` mit systemd-Automount einhängen:

```bash
sudo pacman -S --needed cifs-utils
sudo mkdir -p /mnt/nas/backup
```

Zugangsdaten in `/etc/samba/credentials-nas` (Rechte `600`):

```
username=DEIN_NAS_BENUTZER
password=DEIN_NAS_PASSWORT
```

Zeile in `/etc/fstab` (uid/gid mit `id` prüfen):

```
//nas.local/backup  /mnt/nas/backup  cifs  credentials=/etc/samba/credentials-nas,uid=1000,gid=1000,iocharset=utf8,vers=3.0,_netdev,nofail,x-systemd.automount,x-systemd.idle-timeout=10min  0  0
```

```bash
sudo systemctl daemon-reload
```

```bash
sudo mount -a
```

Danach in der App z. B. `/mnt/nas/backup/iOS-Backups` als Speicherort wählen. Liegt der
Ordner auf einem eingehängten Laufwerk, schaltet die App automatisch die Option
„Nur sichern, wenn … eingehängt ist“ ein. NFS funktioniert genauso.

## Hinweise

- Ein Backup braucht ungefähr so viel Platz wie der belegte Speicher auf dem Gerät.
- Während Backup/Restore das Gerät nicht trennen. Abbrechen ist beim Backup gefahrlos,
  beim Restore nicht zu empfehlen.
- Bei Problemen „Protokoll“ unten rechts aufklappen – dort steht die vollständige
  Ausgabe von `idevicebackup2`.

## Entwicklung / Tests

Die Tests laufen ohne echtes Gerät: `tests/fake_tools/` simuliert die
libimobiledevice-Programme (inkl. Passwortabfragen, Fehlern wie „Wo ist?“ und Abbruch).

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

## Lizenz

MIT
