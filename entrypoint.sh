#!/bin/bash
set -e

viresclient set_token https://vires.services/ows $VIRES_TOKEN
viresclient set_default_server https://vires.services/ows

# Execute the CMD instruction
exec "$@"