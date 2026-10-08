#!/bin/sh
printf 'hello %s; args=%s\n' "$GREETING" "$#"
printf '<%s>\n' "$@"
