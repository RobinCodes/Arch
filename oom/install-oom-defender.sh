#!/usr/bin/env bash
# One-shot: install the OOM Defender and give the machine as much swap as it
# can sensibly use.
#
# Run:  sudo bash install-oom-defender.sh
#   or: sudo SWAPFILE_GB=8 bash install-oom-defender.sh   (pick the file size)
#   or: sudo SWAPFILE_GB=0 bash install-oom-defender.sh   (defender only)
#
# Re-runnable. The daemon, unit and sysctl file are overwritten from this
# folder every time — /etc/oom-defender.conf is not, so tuning survives.
#
# Four things happen here, and they belong together because the defender's
# thresholds are chosen against the swap layout this sets up:
#
#   1. zram grows from half of RAM to all of RAM. It is compressed, so with
#      zstd that costs roughly a third of its size in real memory and buys
#      back the rest. It stays at priority 100 — the fast tier.
#   2. A swapfile on the NVMe, at priority 10, as the slow overflow tier
#      underneath it. This is what stops an allocation wall; the defender is
#      what stops you thrashing your way through it.
#   3. vm tuning to match: page out early and cheaply rather than throwing
#      away the page cache the desktop is built on.
#   4. The defender itself, watching both.
set -euo pipefail

# readlink -f so the sibling files are still found when this is invoked through
# a symlink from somewhere shorter to type.
SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SWAPFILE=/swapfile
# 4G, not 16. The original 16 was sized as "as much as the disk can sensibly
# spare", which on a 116 GB drive that later filled to 90% stopped being
# sensible: the file sat at 0 bytes used because zram (priority 100) takes
# everything first, while costing 16 GB of a disk with 12 GB left. 4G is a
# little over 1x RAM, which is a real overflow tier for the case zram fills,
# and it hands 12 GB back to the filesystem.
SWAPFILE_GB=${SWAPFILE_GB:-4}
KEEP_FREE_GB=20          # disk left over after the swapfile, at minimum
SYSCTL=/etc/sysctl.d/99-oom-defender.conf
ZRAM_CONF=/etc/systemd/zram-generator.conf

if [[ $EUID -ne 0 ]]; then
    echo "Run as root:  sudo bash $0" >&2
    exit 1
fi

for f in oom-defender oom-defender.service oom-defender.conf; do
    [[ -f "$SRC/$f" ]] || { echo "missing $SRC/$f" >&2; exit 1; }
done

# Catch a typo in the daemon here rather than in a restart loop at 3am.
# ast.parse rather than py_compile: running as root, py_compile would leave a
# root-owned __pycache__ sitting in the source folder.
echo ">> Checking the daemon parses"
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$SRC/oom-defender"

# --------------------------------------------------------------------------
# 1. zram: half of RAM -> all of RAM
# --------------------------------------------------------------------------
echo ">> Configuring zram (size = RAM, zstd, priority 100)"
cat >"$ZRAM_CONF" <<'EOF'
# Managed by install-oom-defender.sh
#
# zram-size = ram looks reckless on a 3.4 GB machine and isn't: zram holds
# *compressed* pages, and zstd averages roughly 3:1 on anonymous memory, so a
# full 3.4 GB zram device occupies a bit over a gigabyte of real RAM and hands
# back the difference. The high priority keeps it ahead of the disk swapfile.
[zram0]
zram-size = ram
compression-algorithm = zstd
swap-priority = 100
fs-type = swap
EOF

# --------------------------------------------------------------------------
# 2. The disk swapfile — the slow tier under zram
# --------------------------------------------------------------------------
if [[ $SWAPFILE_GB -gt 0 ]]; then
    avail_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
    max_gb=$(( avail_gb - KEEP_FREE_GB ))
    if [[ -f $SWAPFILE ]]; then
        # Already ours: count its space as available, so re-running with a
        # bigger size can actually grow it.
        cur_gb=$(( $(stat -c %s "$SWAPFILE") / 1024 / 1024 / 1024 ))
        max_gb=$(( max_gb + cur_gb ))
    else
        cur_gb=0
    fi

    if [[ $max_gb -lt 1 ]]; then
        echo "!! Only ${avail_gb}G free on / — skipping the swapfile."
        echo "   zram alone still doubles from here."
        SWAPFILE_GB=0
    elif [[ $SWAPFILE_GB -gt $max_gb ]]; then
        echo "!! ${SWAPFILE_GB}G would leave under ${KEEP_FREE_GB}G free; using ${max_gb}G"
        SWAPFILE_GB=$max_gb
    fi
fi

if [[ $SWAPFILE_GB -gt 0 && $SWAPFILE_GB -ne ${cur_gb:-0} ]]; then
    echo ">> Creating ${SWAPFILE_GB}G $SWAPFILE"
    swapoff "$SWAPFILE" 2>/dev/null || true
    rm -f "$SWAPFILE"
    # ext4 handles fallocated swapfiles fine (btrfs would not).
    fallocate -l "${SWAPFILE_GB}G" "$SWAPFILE"
    chown root:root "$SWAPFILE"
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE" >/dev/null
    swapon -p 10 "$SWAPFILE"
elif [[ $SWAPFILE_GB -gt 0 ]]; then
    echo ">> $SWAPFILE is already ${SWAPFILE_GB}G — leaving it alone"
    swapon -p 10 "$SWAPFILE" 2>/dev/null || true
fi

if [[ $SWAPFILE_GB -gt 0 ]] && ! grep -q "^$SWAPFILE[[:space:]]" /etc/fstab; then
    echo ">> Adding $SWAPFILE to /etc/fstab"
    cp -a /etc/fstab "/etc/fstab.bak-oom-defender"
    printf '\n# Swap overflow under zram. Added by install-oom-defender.sh\n%s none swap defaults,pri=10 0 0\n' \
        "$SWAPFILE" >>/etc/fstab
    # A bad fstab is a rescue shell at next boot. Check before trusting it.
    if ! findmnt --verify --verbose >/dev/null 2>&1; then
        echo "!! findmnt disliked the new /etc/fstab — restoring the backup" >&2
        cp -a /etc/fstab.bak-oom-defender /etc/fstab
        exit 1
    fi
fi

# --------------------------------------------------------------------------
# 3. vm tuning
# --------------------------------------------------------------------------
echo ">> Installing $SYSCTL"
cat >"$SYSCTL" <<'EOF'
# Managed by install-oom-defender.sh

# Above 100 the kernel will evict anonymous pages in preference to file-backed
# ones. That is the right trade when the swap it evicts to is compressed RAM:
# dropping the page cache to keep an idle process resident just means reading
# the same binaries off disk again a second later.
vm.swappiness = 150

# Swap readahead, as a power of two. zram reads are a single page and already
# in memory, so reading eight neighbours costs decompression for nothing.
vm.page-cluster = 0

# Wake kswapd earlier, with more headroom. Reclaim that starts sooner runs in
# the background; reclaim that starts late happens inside your allocation,
# which is what a stall feels like.
vm.watermark_scale_factor = 125

# Roughly 64 MB held back for atomic allocations. Small on most machines,
# meaningful on this one — it is what the kernel has left when everything else
# is spoken for.
vm.min_free_kbytes = 65536
EOF
sysctl -q --system

# --------------------------------------------------------------------------
# 4. The defender
# --------------------------------------------------------------------------
echo ">> Installing the daemon and unit"
install -Dm755 -o root -g root "$SRC/oom-defender" /usr/local/bin/oom-defender
install -Dm644 -o root -g root "$SRC/oom-defender.service" \
    /etc/systemd/system/oom-defender.service
if [[ -f /etc/oom-defender.conf ]]; then
    echo "   /etc/oom-defender.conf exists — keeping your thresholds"
    # Any key absent from the file falls back to the daemon's own default, so
    # an old config keeps working. It will not document settings it has never
    # heard of, though, so drop the current one alongside rather than silently
    # leaving the new options undiscoverable.
    if ! cmp -s "$SRC/oom-defender.conf" /etc/oom-defender.conf; then
        install -Dm644 -o root -g root "$SRC/oom-defender.conf" \
            /etc/oom-defender.conf.new
        missing=()
        while read -r key; do
            grep -qE "^[[:space:]]*$key[[:space:]]*=" /etc/oom-defender.conf \
                || missing+=("$key")
        done < <(grep -oE '^[a-z_]+[[:space:]]*=' "$SRC/oom-defender.conf" \
                 | tr -d ' =' | sort -u)
        if (( ${#missing[@]} )); then
            echo "   new options available (defaults apply until you set them):"
            printf '     %s\n' "${missing[@]}"
        fi
        echo "   documented in /etc/oom-defender.conf.new"
    fi
else
    install -Dm644 -o root -g root "$SRC/oom-defender.conf" /etc/oom-defender.conf
fi

systemctl daemon-reload
systemctl enable --now oom-defender.service
systemctl restart oom-defender.service

# --------------------------------------------------------------------------
# Resize zram last, and only if it is safe to do it live
# --------------------------------------------------------------------------
# Resizing means swapoff, and swapoff has to fault every page currently in
# zram back into RAM. If they don't fit, swapoff either fails or takes the
# machine down with it — the exact thing this whole exercise is against. The
# config is already written, so a reboot picks it up regardless.
zram_used_kb=$(awk '/zram0/ {print $4}' /proc/swaps)
avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
restart_zram() {
    systemctl daemon-reload
    systemctl restart systemd-zram-setup@zram0.service
    # Formatting the device and swapping it on are two different units: the
    # generated dev-zram0.swap requires the setup service, so stopping the
    # service takes the swap down with it and restarting the service does not
    # bring it back synchronously. Start it explicitly, or the summary below
    # reports a machine with no zram on it.
    systemctl start dev-zram0.swap
}

if [[ -z ${zram_used_kb:-} ]]; then
    restart_zram || true
elif (( zram_used_kb + 262144 < avail_kb )); then
    echo ">> Resizing zram now (${zram_used_kb} kB in use, ${avail_kb} kB free)"
    restart_zram
else
    echo "!! Not resizing zram live: ${zram_used_kb} kB is swapped out and only"
    echo "   ${avail_kb} kB is free, so swapoff could stall the machine."
    echo "   The config is written — it takes effect at the next reboot."
fi

echo
echo "============================================================"
swapon --show
echo
free -h
echo
echo "Defender:  systemctl status oom-defender"
echo "Where you stand:  oom-defender --status"
echo "Check notifications reach you:  sudo oom-defender --test-notify"
echo "============================================================"
