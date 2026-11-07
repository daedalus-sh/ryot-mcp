# ryot-mcp: the server, with its whole test suite in checkPhase — config, the
# session (seed, saved, renewed, revoked), every tool against a fake Ryot
# that speaks its GraphQL operations, and the MCP layer through the SDK's own
# client.
{
  lib,
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "ryot-mcp";
  version = "0.2.0";
  pyproject = true;

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./pyproject.toml
      ./README.md
      ./src
      ./tests
    ];
  };

  build-system = [ python3Packages.setuptools ];

  dependencies = with python3Packages; [
    httpx
    mcp
    starlette
    uvicorn
  ];

  nativeCheckInputs = [ python3Packages.pytestCheckHook ];

  pytestFlags = [
    "-p"
    "no:cacheprovider"
  ];

  pythonImportsCheck = [ "ryot_mcp" ];

  meta = {
    description = "MCP server for Ryot: search titles, log what was watched, played and read, rate and review, keep the Watchlist";
    homepage = "https://github.com/daedalus-sh/ryot-mcp";
    license = lib.licenses.mit;
    mainProgram = "ryot-mcp";
    platforms = lib.platforms.linux;
  };
}
