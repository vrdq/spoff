# Maintainer: vrdq
pkgname=spoff
pkgver=0.1.0
pkgrel=1
pkgdesc="Fast, minimalist, dark-monochrome Spotify & YouTube music TUI with offline caching"
arch=('any')
url="https://github.com/vrdq/spoff"
license=('MIT')
depends=(
    'python'
    'python-textual'
    'python-rich'
    'yt-dlp'
    'mpv'
    'python-ytmusicapi'
    'python-pydbus'
    'python-gobject'
    'python-secretstorage'
    'yt-dlp-ejs'
)
optdepends=(
    'nodejs: unscrambles signed-in YouTube streams (or deno/bun)'
    'cava: audio spectrum visualizer'
    'ffmpeg: verify cached audio integrity and duration'
    'wl-clipboard: copy share links on Wayland'
)
makedepends=(
    'python-build'
    'python-installer'
    'python-wheel'
    'python-hatchling'
)
source=("https://files.pythonhosted.org/packages/source/${pkgname::1}/$pkgname/$pkgname-$pkgver.tar.gz")
sha256sums=('3288956b7e44b2e300abacd455ea59c244bcddcddd45d5702d969e16ad79c502')

build() {
    cd "$pkgname-$pkgver"
    python -m build --wheel --no-isolation
}

package() {
    cd "$pkgname-$pkgver"
    python -m installer --destdir="$pkgdir" dist/*.whl
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
