const fs = require("fs");
const path = require("path");
const postcss = require("postcss");
const tailwind = require("@tailwindcss/postcss");
const autoprefixer = require("autoprefixer");

const sharedCssDir = path.resolve(__dirname, "../assets/css/shared");
const adminCssDir = path.resolve(__dirname, "../assets/css/admin");
const publicCssDir = path.resolve(__dirname, "../assets/css/public");
const tempCssDir = path.resolve(__dirname, "../assets/css");

const targets = [
  {
    name: "admin",
    outputPath: path.resolve(__dirname, "../admin.css"),
    inputs: [
      path.join(adminCssDir, "theme.css"),
      path.join(sharedCssDir, "tokens.css"),
      path.join(sharedCssDir, "rich-content.css"),
      path.join(sharedCssDir, "utilities.css"),
      path.join(adminCssDir, "shell.css"),
      path.join(adminCssDir, "components.css"),
      path.join(adminCssDir, "pages.css"),
      path.join(adminCssDir, "responsive.css"),
    ],
  },
  {
    name: "public",
    outputPath: path.resolve(__dirname, "../public.css"),
    inputs: [
      path.join(publicCssDir, "theme.css"),
      path.join(sharedCssDir, "tokens.css"),
      path.join(sharedCssDir, "rich-content.css"),
      path.join(sharedCssDir, "utilities.css"),
      path.join(publicCssDir, "components.css"),
      path.join(publicCssDir, "views.css"),
    ],
  },
];

function joinInputs(inputs) {
  return inputs
    .map((inputPath) => fs.readFileSync(inputPath, "utf8").trim())
    .filter(Boolean)
    .join("\n\n");
}

async function buildTarget({ name, inputs, outputPath }) {
  const input = joinInputs(inputs);
  const tempInputPath = path.join(tempCssDir, `.${name}.tailwind.css`);
  fs.writeFileSync(tempInputPath, input, "utf8");
  try {
    const result = await postcss([tailwind(), autoprefixer()]).process(input, {
      from: tempInputPath,
      to: outputPath,
    });
    fs.writeFileSync(outputPath, result.css, "utf8");
  } finally {
    if (fs.existsSync(tempInputPath)) {
      fs.unlinkSync(tempInputPath);
    }
  }
}

async function build() {
  const args = process.argv.slice(2);
  const selected = args.length === 0 ? "all" : args.length === 2 && args[0] === "--target" ? args[1] : "";
  if (!["all", "admin", "public"].includes(selected)) {
    throw new Error("Usage: build-admin-css.cjs [--target admin|public|all]");
  }
  for (const target of targets.filter((item) => selected === "all" || item.name === selected)) {
    await buildTarget(target);
  }
}

build().catch((error) => {
  console.error(error);
  process.exit(1);
});
