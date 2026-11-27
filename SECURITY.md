# Security

## What runs on your machine

printwright executes CAD scripts written by a language model. Treat them like code from the internet.

Defences in place:

1. **Static guard** (`cad/guard.py`): only geometry and maths modules may be imported (cadquery, OCP, numpy, math,
   trimesh, manifold3d, printwright.cad.organic, a few standard-library helpers). Calls to `open`, `exec`, `eval`,
   `compile`, `__import__`, `getattr`/`setattr`, dunder attribute access and file-writing methods are rejected.
2. **Separate process**: scripts run in a child Python process with a wall-clock timeout (`build_timeout_s`), a CPU
   time limit on POSIX, the project folder as working directory and **no environment variables whose names contain
   KEY, TOKEN, SECRET, PASSWORD, CREDENTIAL or AUTH**.
3. **Claude Code provider**: runs with no tools at all, except the Read tool when reviewing renders, which is
   confined to a temporary folder holding copies of the images; no MCP servers and no user/project settings.

This is **not a sandbox**. A determined script could still find a way around a static check. If you use printwright
with prompts or files you did not write, run it inside a VM or container, and read `model.py` before rebuilding.

## Web server

* Listens on 127.0.0.1 only; `printwright serve` refuses other interfaces.
* Requests with a Host header other than localhost/127.0.0.1 are rejected (DNS-rebinding protection).
* State-changing requests need the `X-Printwright: 1` header, which cross-site pages cannot send without a CORS
  preflight that the server never approves.
* File access is confined to the project folder; app launching is limited to Bambu Studio, Fusion, Blender and
  the file manager.

## Reporting

Please report vulnerabilities privately through GitHub's "Report a vulnerability" on this repository rather than in
a public issue.
