$env:HERMES_DESKTOP_VARIANT = "headless";
$env:HERMES_DESKTOP_LOCAL_ROOT = "D:\VSCodeProjects\hermes-agent";
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/";
$env:HTTP_PROXY = "";
$env:HTTPS_PROXY = "";
npm run build
npx electron-builder --config electron-builder.fork.cjs --win nsis portable --x64 --publish never "-c.directories.output=release-headless"