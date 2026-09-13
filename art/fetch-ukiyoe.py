#!/usr/bin/env python3
"""Build the artwork set: public-domain ukiyo-e from the Met, rendered for this
machine's two screens — the boot splash and the desktop wallpaper.

WHERE THE PICTURES COME FROM, AND WHY THESE

The Metropolitan Museum publishes its open-access collection under CC0 with a
free JSON API and the original scans behind it, typically 3000–6000px wide. No
key, no attribution required, no licence to get wrong. Everything here is
filtered on `isPublicDomain` before it is downloaded, so nothing else can end
up in the set even if a search term drifts.

The queries below are aimed at two registers:

  landscape   Hokusai's Thirty-six Views of Mount Fuji, Hiroshige's One Hundred
              Famous Views of Edo and Fifty-three Stations of the Tōkaidō —
              mist, pampas grass, torii, snow, breaking waves. This is not an
              approximation of the Ghost of Tsushima look; it is where that
              look came from, and the game says so.
  kachō-e     flower-and-bird prints: Hokusai's Large Flowers, Hiroshige's
              bird-and-flower studies, Ohara Koson's seasonal botanicals.

WHY EACH PICTURE IS RENDERED TWICE

An ōban print is usually *portrait*, and both targets here are landscape —
1366x768 for the boot splash, 2560x1440 for the wallpaper. Cropping a tall
print to a wide screen throws away most of the picture, and letterboxing it
onto flat black looks like a mistake. So a portrait original is composed onto
the target instead: the picture fitted whole in the middle, over a heavily
blurred, darkened copy of itself scaled to fill. The background reads as the
print's own paper and ink rather than as a bar, and nothing is cropped away.

Landscape originals are cropped to fill, because there the crop costs nothing.

RE-RUNNING

Idempotent. Already-downloaded objects are skipped by id, so this can be run
again to top the set up after adding a query, and it will only fetch what is
missing. `--force` re-renders everything from the originals without
re-downloading — that is the one to use after changing a size below.
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageEnhance, ImageFilter

API = "https://collectionapi.metmuseum.org/public/collection/v1"

HOME = os.path.expanduser("~")
ROOT = os.path.join(HOME, "Pictures", "ukiyo-e")
ORIGINAL_DIR = os.path.join(ROOT, "original")
WALLPAPER_DIR = os.path.join(ROOT, "wallpaper")
SPLASH_DIR = os.path.join(ROOT, "splash")
INDEX = os.path.join(ROOT, "index.json")

# The wallpaper size is the 4K monitor's mode divided by its scale — big enough
# that noctalia never upscales on either screen. The splash is the laptop
# panel's native mode, which is the one that is actually lit during boot.
WALLPAPER_SIZE = (2560, 1440)
SPLASH_SIZE = (1366, 768)

# Searches, and how many to take from each. Keeping the two registers separate
# is what stops one of them winning the set: a bare "Hiroshige" search returns
# far more landscapes than flower prints, and the request was for both.
QUERIES = [
    ("landscape", "Hokusai Thirty-six Views of Mount Fuji", 5),
    ("landscape", "Hiroshige One Hundred Famous Views of Edo", 6),
    ("landscape", "Hiroshige Fifty-three Stations of the Tokaido", 4),
    ("landscape", "Hiroshige snow moon landscape", 3),
    ("flowers", "Hokusai chrysanthemum peony flowers", 4),
    ("flowers", "Hiroshige bird and flower", 4),
    ("flowers", "Ohara Shoson bird flower", 3),
    ("flowers", "Hiroshige cherry blossom", 3),
    ("flowers", "Japanese woodblock iris wisteria", 3),
]

# A print is what is wanted; the Met's Japanese holdings also include armour,
# lacquer and textiles that match these words perfectly well.
WANTED_CLASSIFICATIONS = ("print", "woodblock")

# Both targets are 16:9, so a print narrower than this cannot fill one without
# either cropping most of it away or sitting as a strip in a sea of blur. The
# tanzaku format — the tall narrow bird-and-flower panels — lands around 0.35
# and looked exactly as bad as that suggests. Ōban tate-e is about 0.68 and is
# fine; ōban yoko-e, the horizontal landscape format both Fuji and Tōkaidō use,
# is about 1.5 and is ideal.
MIN_ASPECT = 0.62

# Rejected after looking at them. The Met's scans are not uniform: some are the
# print alone, some include the mount, and some carry a photographic greyscale
# calibration strip inside the frame, which no amount of trimming removes
# because it is not a border — it sits beside the print. Those, plus the ones
# the search matched on a word rather than a subject.
BLOCKED = {
    "54430",   # Large Lobster — matched "large flowers", is a lobster
    "56797",   # Portrait of a Woman in Large Circle — figure study + grey scale
    "53449",   # Six Jewel Rivers — heavy mount, washed out
    "54303",   # Basket of Flowers with Bamboo Blind — mounted, very pale
    "44981",   # Snow on the Sumida River — calibration strip in frame
    "406537",  # Theodor Bang, Friezes — a German engraver; matched "birds,
    "406538",  #   flowers" and is monochrome European ornament, not ukiyo-e
    "55027",   # Utamaro II, A Courtesan — a figure study, wisteria is backdrop
    "54086",   # Swallows and Wisteria — very pale, mostly blank paper
    "36719",   # Camellia and Bird — pale, wide white margin
    "406539",  # Theodor Bang, Friezes plate 7 — third of the same set
    "55615",   # Torii Kiyonaga, The Iris Garden — a figure study; the irises
               #   are scenery behind three women in kimono
}


def get_json(url, tries=3):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.load(response)
        except Exception as error:
            if attempt == tries - 1:
                print(f"    ! {error}", file=sys.stderr)
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def search(query, limit):
    """Object ids for one query, best-first, already filtered to prints."""
    params = urllib.parse.urlencode({
        "isPublicDomain": "true",
        "hasImages": "true",
        "medium": "Prints",
        "q": query,
    })
    result = get_json(f"{API}/search?{params}")
    if not result or not result.get("objectIDs"):
        return []
    # The API returns relevance order, so walking it in order and stopping at
    # `limit` takes the best matches rather than a random slice of 700.
    return result["objectIDs"][: limit * 6]


def usable(obj):
    if not obj or not obj.get("isPublicDomain"):
        return False
    if not obj.get("primaryImage"):
        return False
    classification = (obj.get("classification") or "").lower()
    object_name = (obj.get("objectName") or "").lower()
    return any(w in classification or w in object_name
               for w in WANTED_CLASSIFICATIONS)


def download(url, path):
    tmp = path + ".part"
    request = urllib.request.Request(
        url, headers={"User-Agent": "ukiyo-e-wallpapers/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response, \
            open(tmp, "wb") as out:
        while chunk := response.read(1 << 16):
            out.write(chunk)
    os.replace(tmp, path)


def compose(image, size):
    """Fit `image` into `size` without cropping anything that matters.

    Landscape originals are cropped to fill — a wide print losing a sliver off
    the ends is invisible. Portrait originals are fitted whole over a blurred,
    darkened copy of themselves, because cropping an ōban print to 16:9 keeps
    about a third of the picture and throws away the composition with it.
    """
    target_w, target_h = size
    src_ratio = image.width / image.height
    target_ratio = target_w / target_h

    if src_ratio >= target_ratio * 0.95:
        # Wide enough to crop: scale to cover, then take the middle.
        scale = max(target_w / image.width, target_h / image.height)
        scaled = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.LANCZOS)
        left = (scaled.width - target_w) // 2
        top = (scaled.height - target_h) // 2
        return scaled.crop((left, top, left + target_w, top + target_h))

    # Taller than the screen: blurred fill behind, whole picture in front.
    cover = max(target_w / image.width, target_h / image.height)
    background = image.resize(
        (max(1, round(image.width * cover)), max(1, round(image.height * cover))),
        Image.LANCZOS)
    left = (background.width - target_w) // 2
    top = (background.height - target_h) // 2
    background = background.crop((left, top, left + target_w, top + target_h))
    # Blur radius scales with the canvas so the same look holds at both sizes.
    background = background.filter(
        ImageFilter.GaussianBlur(radius=target_w / 28))
    background = ImageEnhance.Brightness(background).enhance(0.55)

    contain = min(target_w / image.width, target_h / image.height)
    front = image.resize(
        (max(1, round(image.width * contain)), max(1, round(image.height * contain))),
        Image.LANCZOS)
    background.paste(front, ((target_w - front.width) // 2,
                             (target_h - front.height) // 2))
    return background


def render(original_path, stem, force=False):
    targets = [(WALLPAPER_DIR, WALLPAPER_SIZE, 92),
               (SPLASH_DIR, SPLASH_SIZE, 88)]
    if not force and all(
            os.path.exists(os.path.join(d, stem + ".jpg")) for d, _s, _q in targets):
        return True
    try:
        with Image.open(original_path) as image:
            image = image.convert("RGB")
            for directory, size, quality in targets:
                out = os.path.join(directory, stem + ".jpg")
                composed = compose(image, size)
                composed.save(out, "JPEG", quality=quality, optimize=True)
        return True
    except Exception as error:
        print(f"    ! render failed: {error}", file=sys.stderr)
        return False


def dhash(image, size=16):
    """A difference hash: each bit is "is this pixel brighter than the one to
    its right", over a size x size grid — 256 bits at the default.

    Here to catch the *same print* scanned twice, which the Met holds a lot of
    — two impressions of the Great Wave and two of Red Fuji came back in the
    first run, from different object ids, and as wallpapers they are the same
    picture twice. Comparing hashes rather than bytes is what makes that work:
    the two scans differ in paper tone and a few pixels of registration, and
    agree on everything the eye uses.
    """
    small = image.convert("L").resize((size + 1, size), Image.LANCZOS)
    # tobytes() rather than getdata(): in "L" mode it is the same row-major
    # bytes, and getdata() is itself deprecated as of Pillow 12.
    pixels = small.tobytes()
    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (pixels[base + col] > pixels[base + col + 1])
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


# Below this many differing bits out of 256, two scans are the same print.
#
# Measured rather than guessed, over all 1653 pairs in a 58-image set. The Met
# holds several impressions of the famous prints and they come back under
# different object ids, so the set had six genuine duplicate pairs in it:
#
#     39   Under the Wave off Kanagawa      x2
#     55   Sudden Shower over Shin-Ōhashi   x2
#     59   Fujimigahara in Owari Province   x2
#     66   Storm below Mount Fuji           x3, so three pairs
#     72   "
#     --------------------------------------------------- 78, here
#     86   South Wind, Clear Sky  vs  Storm below Mount Fuji
#     87   Akasaka  vs  Ishiyakushi     (two Tōkaidō stations)
#
# 86 is the closest pair that is genuinely two different pictures — two Hokusai
# Fuji views with the same palette and layout — so the gap between 72 and 86 is
# where this belongs. Note how wide the margin is on both sides: nothing is
# balanced on the edge of it.
#
# An earlier attempt hashed the *composed* wallpaper instead of the original, on
# the theory that composing normalises away the scan margins. It does, and it
# also fills a portrait print's surround with a blur of itself, which is most of
# what an 8x8 grid sees — nine unrelated Hiroshige verticals came out as
# "duplicates" of one another. Hash the picture, not the frame it is put in.
DUPLICATE_DISTANCE = 78


def accept(path, hashes):
    """Look at the downloaded file and decide whether it earns a place.

    Returns its hash, or None to reject. Rejection deletes the file: a scan
    that is not going on screen is not worth the disk, and leaving it behind
    would have the next --force run render it again.
    """
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            ratio = image.width / image.height
            if ratio < MIN_ASPECT:
                return None, f"too narrow ({ratio:.2f})"
            digest = dhash(image)
    except Exception as error:
        return None, f"unreadable ({error})"

    for other, other_digest in hashes.items():
        if hamming(digest, other_digest) <= DUPLICATE_DISTANCE:
            return None, f"same print as {other}"
    return digest, ""


def load_index():
    try:
        with open(INDEX, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_index(index):
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, INDEX)


def slug(text, limit=52):
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    out = "".join(keep)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:limit] or "untitled"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--force", action="store_true",
                        help="re-render from the originals, without re-downloading")
    parser.add_argument("--prune", action="store_true",
                        help="re-test what is already here against the current "
                             "rules and drop what no longer passes")
    args = parser.parse_args()

    for directory in (ORIGINAL_DIR, WALLPAPER_DIR, SPLASH_DIR):
        os.makedirs(directory, exist_ok=True)

    index = load_index()

    if args.prune:
        # Applying today's rules to yesterday's downloads. The blocklist and
        # the aspect floor were both written after looking at a set that had
        # already been fetched, and without this they would only ever affect
        # the next new picture.
        removed = 0
        hashes = {}
        for object_id in sorted(index, key=lambda k: (index[k]["register"], k)):
            entry = index[object_id]
            path = os.path.join(ORIGINAL_DIR, entry["file"])
            why = ""
            if object_id in BLOCKED:
                why = "blocked"
            elif not os.path.exists(path):
                why = "original missing"
            else:
                digest, why = accept(path, hashes)
                if digest is not None:
                    hashes[object_id] = digest
            if not why:
                continue
            print(f"   - {entry['artist'][:20]:20} {entry['title'][:40]:40} {why}")
            for directory, suffix in ((ORIGINAL_DIR, ""),
                                      (WALLPAPER_DIR, ".jpg"),
                                      (SPLASH_DIR, ".jpg")):
                target = (path if not suffix
                          else os.path.join(directory, entry["stem"] + suffix))
                try:
                    os.remove(target)
                except OSError:
                    pass
            del index[object_id]
            removed += 1
        save_index(index)
        print(f"\n   dropped {removed}, {len(index)} left")
        return

    if args.force:
        print(f">> Re-rendering {len(index)} originals")
        for object_id, entry in sorted(index.items()):
            path = os.path.join(ORIGINAL_DIR, entry["file"])
            if os.path.exists(path):
                render(path, entry["stem"], force=True)
        print("   done")
        return

    have = set(index)

    # Hashes of what is already kept, so a re-run cannot add a second scan of a
    # print the set already has.
    hashes = {}
    for object_id, entry in index.items():
        path = os.path.join(ORIGINAL_DIR, entry["file"])
        if not os.path.exists(path):
            continue
        try:
            with Image.open(path) as image:
                hashes[object_id] = dhash(image.convert("RGB"))
        except Exception:
            pass

    for register, query, want in QUERIES:
        print(f">> {register:9} {query}")
        taken = 0
        for object_id in search(query, want):
            if taken >= want:
                break
            if str(object_id) in have or str(object_id) in BLOCKED:
                continue
            obj = get_json(f"{API}/objects/{object_id}")
            time.sleep(0.12)          # the Met asks for politeness, not a key
            if not usable(obj):
                continue

            title = obj.get("title") or f"met-{object_id}"
            artist = obj.get("artistDisplayName") or "unknown"
            stem = f"{slug(artist, 24)}-{slug(title, 46)}-{object_id}"
            extension = os.path.splitext(
                urllib.parse.urlparse(obj["primaryImage"]).path)[1] or ".jpg"
            filename = stem + extension
            path = os.path.join(ORIGINAL_DIR, filename)

            if not os.path.exists(path):
                try:
                    download(obj["primaryImage"], path)
                except Exception as error:
                    print(f"    ! {title[:48]}: {error}", file=sys.stderr)
                    continue

            digest, why = accept(path, hashes)
            if digest is None:
                print(f"   - {title[:44]:44} {why}")
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            hashes[str(object_id)] = digest

            if not render(path, stem):
                continue

            index[str(object_id)] = {
                "title": title,
                "artist": artist,
                "date": obj.get("objectDate") or "",
                "register": register,
                "file": filename,
                "stem": stem,
                "url": obj.get("objectURL") or "",
            }
            have.add(str(object_id))
            taken += 1
            print(f"   + {artist[:22]:22} {title[:46]}")
        save_index(index)

    save_index(index)
    landscapes = sum(1 for e in index.values() if e["register"] == "landscape")
    flowers = len(index) - landscapes
    print(f"\n{len(index)} artworks — {landscapes} landscape, {flowers} flowers")
    print(f"  originals   {ORIGINAL_DIR}")
    print(f"  wallpapers  {WALLPAPER_DIR}   {WALLPAPER_SIZE[0]}x{WALLPAPER_SIZE[1]}")
    print(f"  splashes    {SPLASH_DIR}   {SPLASH_SIZE[0]}x{SPLASH_SIZE[1]}")


if __name__ == "__main__":
    random.seed()
    main()
