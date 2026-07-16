# egptctl

The CLI for [AgentOS](https://docs.agno.com), built for humans and coding agents.

Install the `egptctl` distribution from the workspace or your configured package
index, then run the branded `egpt` command:

```bash
pip install -e libs/egptctl
egpt --help
```

Once `egptctl` is available from the configured package index, name the
distribution explicitly for an ephemeral invocation:

```bash
uvx --from egptctl egpt --help
```

Do not use `uvx egpt`: the public `egpt` distribution is unrelated to this CLI.
