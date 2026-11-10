"""Run independently: python terminal_main.py [--transport stdio]."""
import argparse
import os

from terminal_mcp.auth import principal,verify
from terminal_mcp.config import settings
from terminal_mcp.server import build_server,http_app


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport',choices=['http','stdio'],default='http')
    args=parser.parse_args()
    config=settings()
    mcp,_=build_server(config)
    if args.transport=='stdio':
        principal.set(verify(os.environ.get('TERMINAL_STDIO_TOKEN',''),config.terminal_shared_secret.get_secret_value()))
        mcp.run(transport='stdio')
    else:
        import uvicorn
        uvicorn.run(http_app(mcp,config),host=config.terminal_host,port=config.terminal_port,proxy_headers=False)


if __name__=='__main__':main()
