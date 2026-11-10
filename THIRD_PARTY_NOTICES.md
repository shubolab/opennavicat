# Third-party notices

OpenNavicat's own source is distributed under the MIT License in `LICENSE`.
Dependencies remain covered by their respective licenses. The entries below
identify the packages used by this application; installed wheel metadata is the
authority for the exact version and accompanying license files on each build host.

| Component | Use | License / source |
| --- | --- | --- |
| Qt for Python / PySide6 Essentials / Shiboken6 | Native Qt Widgets GUI and bindings | Installed 6.12.0 metadata: LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only. [Qt for Python license documentation](https://doc.qt.io/qtforpython-6/licenses.html), [upstream source](https://code.qt.io/pyside/pyside-setup.git/). |
| Qt libraries and plugins | Native GUI runtime | Individual Qt modules and bundled third-party components retain their own notices. [Qt licensing](https://doc.qt.io/qt-6/licensing.html), [Qt source](https://code.qt.io/). |
| Python | Interpreter and standard library, including sqlite3 | Python Software Foundation license and included third-party notices. [Python license](https://docs.python.org/3/license.html), [source](https://github.com/python/cpython). |
| SQLite | Embedded database through Python | Public domain dedication. [SQLite copyright](https://sqlite.org/copyright.html), [source](https://sqlite.org/src). |
| Psycopg / psycopg-binary | PostgreSQL driver and bundled client libraries | LGPL-3.0-only for Psycopg. Binary wheels include client libraries with separate notices. [Psycopg source](https://github.com/psycopg/psycopg), [installation/build variants](https://www.psycopg.org/psycopg3/docs/basic/install.html). |
| PostgreSQL libpq | PostgreSQL client runtime, bundled by binary driver | PostgreSQL License. [PostgreSQL license](https://www.postgresql.org/about/licence/). |
| PyMySQL | MySQL and MariaDB driver | MIT. [Source and license](https://github.com/PyMySQL/PyMySQL). |
| Paramiko | SSH authentication, host-key validation and TCP forwarding | LGPL-2.1-or-later (as stated in the shipped source headers). [Source and license](https://github.com/paramiko/paramiko). |
| bcrypt / PyNaCl | Encrypted SSH private keys and SSH cryptographic algorithms | bcrypt: Apache-2.0; PyNaCl: Apache-2.0. [bcrypt source](https://github.com/pyca/bcrypt), [PyNaCl source](https://github.com/pyca/pynacl). Bundled libsodium retains its own ISC license and notices. |
| cryptography / OpenSSL and associated runtime dependencies | Authentication and TLS support | cryptography: Apache-2.0 OR BSD-3-Clause. OpenSSL uses its version-specific license. [cryptography source](https://github.com/pyca/cryptography), [OpenSSL source](https://github.com/openssl/openssl). |
| sqlparse | SQL statement tokenization and formatting | BSD-3-Clause. [Source and license](https://github.com/andialbrecht/sqlparse). |
| keyring and platform backends | OS-managed credential storage | MIT for keyring; backend dependencies retain separate licenses. [Source](https://github.com/jaraco/keyring). |
| PyInstaller | Desktop executable packaging | GPL with bootloader exception; selected files Apache-2.0. [License and exception](https://pyinstaller.org/en/stable/license.html). |

This project uses unmodified binary wheels from the configured Python package
index. Qt and driver libraries remain separate dynamically linked components in
the directory/application bundle. The Python package, editable source and build
recipe are supplied so the application can also be run with independently
installed compatible libraries. No dependency is represented as being licensed
under OpenNavicat's MIT License.

The build recipe includes this notice, the application license and license files
provided by installed distribution metadata in the generated application.
Windows ARM64 builds use psycopg's Python implementation with a native libpq and
OpenSSL build from vcpkg; they do not depend on an emulated x64 PostgreSQL client.
Platform-specific bundled libraries should be inventoried again for each release;
the dependency set can vary between Windows, Linux and macOS.

Navicat and PremiumSoft names identify the locally installed reference examined
during development. No Navicat program code, artwork or icons are application
dependencies or distributed application assets. Raw local investigation evidence
is not part of the desktop distribution.
