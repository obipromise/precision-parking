#!/bin/sh
# Rebuilds index.html (standalone page) from precision-parking.html (artifact source).
cd "$(dirname "$0")"
{ printf '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0d1115"></head><body>\n'; cat precision-parking.html; printf '\n</body></html>\n'; } > index.html
