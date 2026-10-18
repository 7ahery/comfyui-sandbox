# Runs as ROOT inside the distro, right after it is created. Idempotent.
# Creates the Linux user with no password and no sudo. A password would guard
# nothing: from Windows, `wsl -d <distro> -u root` opens a root shell without one,
# and the docker group is root-equivalent anyway. Root tasks use `wsl -u root`.
# Required env: LINUX_USER
: "${LINUX_USER:?LINUX_USER is required}"
printf '%s' "$LINUX_USER" | grep -qE '^[a-z][a-z0-9_-]{0,31}$' \
  || { echo "[error] bad Linux user name: $LINUX_USER" >&2; exit 1; }

if id "$LINUX_USER" >/dev/null 2>&1; then
  echo "[ok] user $LINUX_USER already exists"
else
  # useradd leaves the password locked: no password login at all.
  useradd --create-home --shell /bin/bash "$LINUX_USER"
  echo "[ok] user $LINUX_USER created (no password)"
fi
groups=" $(id -nG "$LINUX_USER") "
for group in sudo admin; do
  case "$groups" in
    *" $group "*)
      gpasswd -d "$LINUX_USER" "$group" >/dev/null
      echo "[ok] removed $LINUX_USER from $group";;
  esac
done
id "$LINUX_USER"
