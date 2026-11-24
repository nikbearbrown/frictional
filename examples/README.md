# Examples

`word-counter/` is a small sample project made up for trying Frictional. It is not student work. Its FRICTIONAL.md and PREDICTIONS.md are constructed examples of the course templates.

Try the audit on it first, to check your setup:

```sh
./frictional audit examples/word-counter --out ~/frictional-example-1
./frictional audit examples/word-counter --out ~/frictional-example-2 --run
```

`word-counter-starter/` is a made-up course starter for the same project, for trying `frictional probe make` (see the main README).

The second command needs Docker or Podman running. It installs Flask in a fresh Linux container from the project's own `frictional-run.json`, runs the four tests, and checks that the web page answers.
