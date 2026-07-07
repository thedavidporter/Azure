#!/usr/bin/env bash
set -e

echo "Installing Python dependencies..."
pip install requests playwright tabulate

echo "Installing Playwright's Chromium browser..."
playwright install chromium

echo ""
echo "Done. Next steps:"
echo ""
echo "  1. Get a free Kroger API key at: https://developer.kroger.com"
echo "     Then set your credentials:"
echo "       export KROGER_CLIENT_ID='your-client-id'"
echo "       export KROGER_CLIENT_SECRET='your-client-secret'"
echo ""
echo "  2. Run the checker:"
echo "       python ~/grocery_price_checker.py \"milk, eggs, bread, butter, chicken breast\""
echo ""
echo "  (Without Kroger credentials the script still runs Meijer and ALDI.)"
