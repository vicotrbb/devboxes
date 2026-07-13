import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ignoredDirectories = new Set([
  ".git",
  ".mypy_cache",
  ".pytest_cache",
  ".ruff_cache",
  ".venv",
  "node_modules",
  "target",
]);
const proseExtensions = new Set([".html", ".md"]);
const markdownLinkPattern = /!?\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/gu;
const errors = [];

function collectFiles(path) {
  if (!existsSync(path)) {
    return [];
  }
  if (!statSync(path).isDirectory()) {
    return proseExtensions.has(extname(path)) ? [path] : [];
  }
  return readdirSync(path, { withFileTypes: true }).flatMap((entry) => {
    if (entry.isDirectory() && ignoredDirectories.has(entry.name)) {
      return [];
    }
    return collectFiles(resolve(path, entry.name));
  });
}

const proseFiles = collectFiles(repositoryRoot);

for (const file of proseFiles) {
  const content = readFileSync(file, "utf8");
  if (content.includes("\u2014")) {
    errors.push(`${file}: em dash punctuation is not allowed`);
  }
  if (content.includes("\u2013")) {
    errors.push(
      `${file}: en dash punctuation is not allowed, use words or commas`,
    );
  }
  if (extname(file) !== ".md") {
    continue;
  }
  for (const match of content.matchAll(markdownLinkPattern)) {
    const destination = match[1].replace(/^<|>$/gu, "");
    if (
      destination.startsWith("#") ||
      destination.startsWith("mailto:") ||
      /^[a-z][a-z\d+.-]*:\/\//iu.test(destination)
    ) {
      continue;
    }
    const localPath = decodeURIComponent(destination.split("#", 1)[0]);
    if (localPath && !existsSync(resolve(dirname(file), localPath))) {
      errors.push(`${file}: local link target does not exist: ${destination}`);
    }
  }
}

if (errors.length > 0) {
  for (const error of errors) {
    console.error(error);
  }
  process.exitCode = 1;
} else {
  console.log(`Documentation checks passed for ${proseFiles.length} files.`);
}
