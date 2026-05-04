"""
Standalone webui server for Electron wrapper.
Run with: python -m pipedbg.webui_server
"""
import sys
import argparse
from pipedbg.webui import run_ui_server

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='pipedbg web UI server')
    parser.add_argument('workflow', nargs='?', default='pipedbg/python-ci.yml',
                        help='Path to workflow file')
    parser.add_argument('--host', default='127.0.0.1', help='Server host')
    parser.add_argument('--port', type=int, default=8765, help='Server port')
    parser.add_argument('--no-open', action='store_true', help='Do not open browser')
    
    args = parser.parse_args()
    
    try:
        run_ui_server(
            workflow_path=args.workflow,
            host=args.host,
            port=args.port,
            open_browser=not args.no_open
        )
    except KeyboardInterrupt:
        print('\nServer stopped.')
        sys.exit(0)
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)
