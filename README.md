# Overview

## Architecture
There are 3 processes here:
- a websocket server (fake nodex)
- a websocket client and http request forwarder (cluster agent)
- a web server (fake node agent/controller)

## Quickstart

In a terminal run (fake node agent/controller):
```
python web_server.py
```

In another terminal run (fake nodex):
```
python websocket_server.py
```

In another terminal run (websocket client):
```
python websocket_client.py
```