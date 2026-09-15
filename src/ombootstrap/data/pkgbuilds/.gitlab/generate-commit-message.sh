#!/bin/bash
set -e

find_pkg_files() {
    local filter_arg="${1:-AR}"
    git diff --cached --name-only --diff-filter="$filter_arg" | grep '\.pkg\.tar\.'
}
get_pkg_attr() {
    local attr="$1"
    local dir="$2"
    grep "$attr = " "$dir"/.PKGINFO | cut -d " " -f 3
}

declare -A packages
declare -A packages_new
declare -A packages_upgraded
declare -A packages_deleted
# work on new packages first, then deleted ones
for file in $(find_pkg_files D); do
    repo="$(dirname "$file")"
    dir=$(mktemp -d)
    git show "HEAD:$file" | bsdtar -C "$dir" -x .PKGINFO
    pkgname="$(get_pkg_attr pkgname "$dir")"
    pkgver="$(get_pkg_attr pkgver "$dir")"
    identifier="$repo/$pkgname"
    packages_deleted["$identifier"]="$pkgver"
    rm -r "$dir"
done

for file in $(find_pkg_files); do
    repo="$(dirname "$file")"
    dir=$(mktemp -d)
    bsdtar -xf "$file" -C "$dir" .PKGINFO
    pkgname="$(get_pkg_attr pkgname "$dir")"
    pkgver="$(get_pkg_attr pkgver "$dir")"
    identifier="$repo/$pkgname"
    packages["$identifier"]="$pkgver"
    if [[ -n "${packages_deleted["$identifier"]}" ]]; then
        # pkg had an older version deleted -> it was upgraded
        packages_upgraded["$identifier"]="from ${packages_deleted["$identifier"]} -> ${pkgver}"
        # drop pkg from deleted
        unset packages_deleted["$identifier"]
    else
        packages_new["$identifier"]="$pkgver"
    fi
    rm -r "$dir"
done
pkgbuilds_dir="$(dirname "${BASH_SOURCE[0]}")"
commithash="$(cd "$pkgbuilds_dir" && git rev-parse HEAD)"
committag="$(cd "$pkgbuilds_dir" && ( git describe --tags --abbrev=4 HEAD || (echo "(couldn't retrieve git tag)" | tee /dev/stderr) ) )"
echo "Build for pkgbuilds.git commit $commithash, $committag: ${#packages[@]} pkgs"
if (( ${#packages_new[@]} )); then
    echo  # empty line to mark paragraph for git
    echo "Added:"
    for pkg in "${!packages_new[@]}"; do
        echo "- new pkg $pkg: ${packages_new[$pkg]}"
    done
fi
if (( ${#packages_deleted[@]} )); then
    echo  # empty line as separator
    echo "Removed:"
    for pkg in "${!packages_deleted[@]}"; do
        echo "- dropped pkg $pkg: ${packages_deleted[$pkg]}"
    done
fi
if (( ${#packages_upgraded[@]} )); then
    echo  # empty line as separator
    echo "Updated:"
    for pkg in "${!packages_upgraded[@]}"; do
        echo "- update $pkg: ${packages_upgraded[$pkg]}"
    done
fi
