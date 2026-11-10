# Windows ARM64 uses native libpq with psycopg's supported ctypes implementation.
$ErrorActionPreference = 'Stop'
if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'Arm64') {
    throw 'This build is only for a native Windows ARM64 host.'
}
$vcpkgRoot = $env:VCPKG_INSTALLATION_ROOT
if (-not $vcpkgRoot) { throw 'VCPKG_INSTALLATION_ROOT is not set.' }
$tool = Join-Path $vcpkgRoot 'vcpkg.exe'
& $tool install 'libpq[core,openssl]:arm64-windows' --clean-after-build --disable-metrics
if ($LASTEXITCODE -ne 0) { throw 'The native ARM64 libpq build failed.' }
$libraryDirectory = Join-Path $vcpkgRoot 'installed/arm64-windows/bin'
if (-not (Test-Path (Join-Path $libraryDirectory 'libpq.dll'))) { throw 'libpq.dll was not produced.' }
$env:OPENNAVICAT_LIBPQ_DIR = $libraryDirectory
$env:PATH = "$libraryDirectory;$env:PATH"
$nativePrefix = Join-Path $vcpkgRoot 'installed/arm64-windows'
# cryptography has no Windows ARM64 wheel: its Rust build must link the same
# native OpenSSL libraries that were just built for libpq.
$settings = @{
    OPENNAVICAT_LIBPQ_DIR = $libraryDirectory
    VCPKG_ROOT = $vcpkgRoot
    VCPKGRS_TRIPLET = 'arm64-windows'
    VCPKGRS_DYNAMIC = '1'
    OPENSSL_DIR = $nativePrefix
    OPENSSL_LIB_DIR = (Join-Path $nativePrefix 'lib')
    OPENSSL_INCLUDE_DIR = (Join-Path $nativePrefix 'include')
    OPENSSL_STATIC = '0'
    PYTHONPATH = (Join-Path $PSScriptRoot 'windows-runtime-site')
}
foreach ($name in $settings.Keys) {
    [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
    if ($env:GITHUB_ENV) { "$name=$($settings[$name])" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8 }
}
if ($env:GITHUB_PATH) { $libraryDirectory | Out-File -FilePath $env:GITHUB_PATH -Append -Encoding utf8 }
& git -C $vcpkgRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Could not record the vcpkg source revision.' }
