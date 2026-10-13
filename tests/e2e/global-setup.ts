import { execFileSync } from "node:child_process";
import path from "node:path";

export default async function globalSetup() {
  const root = path.resolve(__dirname, "../..");
  execFileSync(path.join(root, "backend/.venv/bin/python"), [path.join(root, "scripts/make_fixtures.py"), path.join(__dirname, "fixtures")], { stdio: "inherit" });
}
