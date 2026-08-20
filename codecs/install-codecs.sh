#!/usr/bin/env bash
# One-shot: the media packages a stock Arch + XFCE install leaves out.
# Run:  sudo bash install-codecs.sh
#
# What was actually missing, measured rather than assumed. MOV plays already:
# qtdemux (the QuickTime demuxer) ships in gst-plugins-good, and gst-libav
# decodes what is inside it, so H.264, HEVC, ProRes and AC-3 in a .mov all
# decode today -- in software, on all four cores of an N-series part.
#
# So this is not "make MOV work". It is the three gaps around it:
#
#   gst-plugin-va       Hardware decode. The iHD VAAPI driver and the kernel
#                       side are already here; nothing exposes them to
#                       GStreamer, so there is not one hardware decoder
#                       registered. This adds vah264dec/vah265dec/vavp9dec/
#                       vaav1dec, which is the difference between a 4K phone
#                       .mov playing and a slideshow that empties the battery.
#   gst-plugins-bad     The plugin package. Only gst-plugins-bad-*libs* was
#   gst-plugins-ugly    installed, which is why h264parse and h265parse exist
#                       while the decoders that ship alongside them do not.
#                       Between them: the older and more awkward formats a
#                       download can still be in.
#   ffmpegthumbnailer   Video thumbnails in Thunar. tumbler is installed and
#                       has no video thumbnailer to call, so every .mov in a
#                       folder is a generic film-strip icon.
#   libva-utils         vainfo, so "is hardware decode actually on" is a
#                       question with an answer. Diagnostics only.
#
# Nothing here replaces a codec that was working; every package adds elements
# next to what is already registered, and decodebin keeps the software path as
# the fallback it always was.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo:  sudo bash $0" >&2
    exit 1
fi

PACKAGES=(
    gst-plugin-va
    gst-plugins-bad
    gst-plugins-ugly
    ffmpegthumbnailer
    libva-utils
)

echo ">> Installing: ${PACKAGES[*]}"
pacman -S --needed --noconfirm "${PACKAGES[@]}"

echo
echo ">> Hardware decoders now registered with GStreamer:"
# Run as the invoking user: the plugin registry is per-user and building it as
# root leaves a root-owned cache in $HOME that the session then cannot write.
if [[ -n ${SUDO_USER:-} ]]; then
    runuser -u "$SUDO_USER" -- gst-inspect-1.0 2>/dev/null |
        grep -E '^va: *va(h264|h265|vp9|av1|mpeg2)dec' || echo "   (none -- see vainfo below)"
    echo
    echo ">> VAAPI profiles the GPU reports:"
    runuser -u "$SUDO_USER" -- vainfo 2>&1 | grep -E 'Driver version|VAProfileH264Main|VAProfileHEVCMain' || true
fi

echo
echo "============================================================"
echo "Done. Video thumbnails appear as Thunar regenerates them."
echo "Restart Parole to pick up the new decoders."
echo "============================================================"
