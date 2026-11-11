"""Manifest-owned installation lifecycle CLI; reads have no predispatch writes."""
import json
import os
from sandbox.registry import CommandSpec, register_specs
from sandbox.installation.binding import (
    InstallationBindingError, initialize_installation, read_installation_binding,
)


def configure(parser):
    sub = parser.add_subparsers(dest='installation_action', required=True)
    for action in ('status', 'initialize'):
        child = sub.add_parser(action)
        child.add_argument('--json', action='store_true')
        child.add_argument('--home', default=None)
        if action == 'initialize':
            child.add_argument('--layout', choices=('owner', 'managed'), default='owner')
            child.add_argument('--ownership-marker', default=None)
            child.add_argument('--confirm', action='store_true')


def command(args):
    try:
        if args.installation_action == 'initialize':
            value = initialize_installation(home=args.home, layout_kind=args.layout,
                ownership_marker=args.ownership_marker or os.environ.get('SANDBOX_REMOTE_MCP_MARKER', ''),
                confirm=args.confirm)
        else:
            value = read_installation_binding(home=args.home)
        payload = {'ok': True, 'installation_schema': 1,
                   'identity' if args.installation_action == 'initialize' else 'binding': value,
                   'error': None}
    except InstallationBindingError as exc:
        payload = {'ok': False, 'installation_schema': 1, 'binding': None,
                   'error': {'code': exc.code, 'message': str(exc)}}
    print(json.dumps(payload))
    if not payload['ok']:
        raise SystemExit(1)


register_specs((CommandSpec('installation', command, configure=configure,
    owner=__name__, scope='global', predispatch_policy=lambda _args: True,
    help='inspect or explicitly initialize genuine installation authority'),))
