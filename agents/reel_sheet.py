"""Production sheet for a Reel, in Bahasa Indonesia, in the structure of the Vox-style brief:
A. three alternative titles   B. opening hook   C. full narration   D. storyboard table
E. animation instructions per scene   F. visual assets and sound design   G. editing and pacing guidelines
plus the references behind every factual claim and which statements are interpretation. Written by code from the
same JSON the renderer uses, so the sheet can never disagree with the video. Zero cost.
"""
import re

ACT_NAME = {"hook": "Pertanyaan pembuka", "context": "Konteks", "stakes": "Kenapa penting", "evidence": "Bukti",
            "reveal": "Kejutan / penjelasan", "conclusion": "Kesimpulan", "cta": "Ajakan"}
CAMERA_TEXT = {
    "push_in": "kamera perlahan mendekat (zoom-in 1,0x ke 1,3x) agar mata tertuju ke detail",
    "pull_out": "kamera menjauh (1,3x ke 1,0x) untuk memperlihatkan gambaran besar",
    "pan_left": "kamera bergeser dari kanan ke kiri di atas gambar (pan)",
    "pan_right": "kamera bergeser dari kiri ke kanan di atas gambar (pan)",
    "zoom_to_detail": "zoom cepat ke bagian terpenting (1,0x ke 1,7x)",
    "static": "kamera diam; gerak hanya dari elemen grafis",
}
TRANSITION_TEXT = {"wipe": "wipe ungu dari kiri ke kanan (0,3 dtk)", "slide": "scene baru naik dari bawah (0,3 dtk)",
                   "fade": "memudar dari warna scene sebelumnya (0,3 dtk)", "cut": "potong langsung"}
VIZ_TEXT = {
    "stat": "angka besar muncul dulu, lalu label dan keterangan menyusul",
    "share": "batang proporsi tumbuh dari kiri, tiap bagian diberi nama bergantian",
    "compare": "batang perbandingan tumbuh serentak, nilai muncul di ujungnya",
    "dots": "titik-titik muncul satu per satu; titik sorotan menyala terakhir",
    "steps": "langkah muncul berurutan dengan panah penghubung",
    "timeline": "garis waktu digambar dari atas ke bawah; tiap peristiwa muncul saat garis sampai",
}
HEDGE = re.compile(r"\b(menurut|kemungkinan|diduga|tampaknya|mungkin|curiga|para peneliti menduga)\b", re.I)


def _visual(b):
    if b.get("viz"):
        v = b["viz"]
        return f"Infografis `{v.get('kind')}` (digambar kode)"
    if b.get("image_query"):
        return f"Foto dokumenter asli: \"{b['image_query']}\" (Wikimedia Commons / NASA, berlisensi)"
    return f"Stiker doodle besar `{b.get('icon') or 'otomatis dari kata kunci'}` dengan percikan"


def _clean(s):
    return (s or "").replace("|", "/").replace("\n", " ")


def sheet(post, timings=None, credits=None):
    r = post["reel"]
    beats = r["beats"]
    claims = post.get("claims") or []
    timings = timings or [0.0] * len(beats)
    total = sum(timings)
    titles = r.get("titles") or [r.get("hook", ""), post.get("topic", ""), (beats[0].get("text") or "")]
    out = [f"# Lembar produksi Reel: {post.get('topic', '')}", "",
           f"Format: Instagram Reels 9:16 (1080 x 1920, 30 fps), Bahasa Indonesia, gaya jurnalisme visual ala Vox. "
           f"Durasi: {total:.0f} detik (batas 60 detik). Nada: bercerita. Penonton: anak muda.", ""]
    if r.get("central_question"):
        out += [f"**Pertanyaan utama:** {r['central_question']}", ""]
    out += ["## A. Tiga alternatif judul", ""] + [f"{i}. {t}" for i, t in enumerate(titles, 1)] + [""]
    out += ["## B. Hook pembuka (3-5 detik pertama)", "",
            f"Teks di layar: **{r.get('hook', '')}**", "", f"Narasi: \"{beats[0]['say']}\"", ""]
    out += ["## C. Naskah narasi lengkap", ""]
    for i, b in enumerate(beats, 1):
        out.append(f"{i}. *{ACT_NAME.get(b.get('act'), 'Scene')}*: {b['say']}")
    out += ["", "## D. Storyboard", "",
            "| # | Durasi | Narasi | Visual utama | Gerak dan animasi | Teks di layar | Transisi ke scene berikut | Aset |",
            "|---|---|---|---|---|---|---|---|"]
    for i, b in enumerate(beats, 1):
        nxt = beats[i] if i < len(beats) else None
        trans = TRANSITION_TEXT.get(nxt.get("transition"), "otomatis (wipe/slide/fade bergantian)") if nxt else "selesai"
        cam = CAMERA_TEXT.get(b.get("camera"), "kamera mendekat atau bergeser perlahan")
        asset = "Stiker + percikan"
        if b.get("viz"):
            asset = "Infografis kode"
        elif b.get("image_query"):
            asset = "Foto berlisensi"
        out.append(f"| {i} | {timings[i - 1]:.1f} dtk | {_clean(b['say'])} | {_clean(_visual(b))} | {_clean(cam)} | "
                   f"{_clean(b.get('text') or '')} | {trans} | {asset} |")
    out += ["", "## E. Instruksi animasi per scene", ""]
    for i, b in enumerate(beats, 1):
        v = b.get("viz")
        line = [f"### Scene {i} ({ACT_NAME.get(b.get('act'), 'scene')}, {timings[i - 1]:.1f} dtk)",
                f"- Judul bergerak: \"{(b.get('text') or '').upper()}\" muncul kata demi kata dengan efek pop-in (easeOutBack, 0,35 dtk); stabilo kuning menyapu di belakangnya (0,4 dtk).",
                f"- Visual: {_visual(b)}. Muncul dengan skala 0,55 ke 1,0 (pop, 0,45 dtk)."]
        if v:
            line.append(f"- Animasi data: {VIZ_TEXT.get(v.get('kind'), '')}.")
        if b.get("image_query"):
            line.append(f"- Kamera: {CAMERA_TEXT.get(b.get('camera'), CAMERA_TEXT['push_in'])}.")
        line.append("- Lapisan parallax: tiga bentuk datar di latar bergerak dengan kecepatan berbeda selama scene (kedalaman tanpa 3D).")
        line.append("- Caption: 3-4 kata, kata yang sedang diucapkan disorot kuning, sinkron dengan suara (timestamp per kata).")
        if b.get("claim"):
            line.append(f"- Label sumber di kiri bawah setelah 0,8 dtk: \"Sumber: {claims[b['claim'] - 1].get('source_name', '')}\".")
        out += line + [""]
    out += ["## F. Aset visual dan sound design", ""]
    out += ["**Visual**", "- Foto: dicari otomatis dari Wikimedia Commons dan NASA, hanya lisensi yang boleh dipakai ulang; kredit tercetak di video dan di caption."]
    for c in credits or []:
        out.append(f"  - {c}")
    out += ["- Infografis, stiker doodle, maskot Faktu, tipografi bergerak: digambar oleh kode (tanpa berkas unduhan).",
            "- Peta animasi, kliping koran, dan footage arsip bergerak belum tersedia di mesin ini (butuh data peta dan arsip berlisensi); pemakaian foto dokumenter dengan gerak kamera menggantikannya.",
            "", "**Suara**",
            "- Narasi: suara Indonesia neural `id-ID-ArdiNeural` (edge-tts), kecepatan +3%, jeda 0,15 dtk sebelum dan 0,45 dtk sesudah tiap kalimat.",
            f"- Musik latar: dibuat kode (pad akor + arpeggio, suasana `{r.get('mood', 'curious')}`), volume 16% di bawah suara; orisinal sehingga bebas klaim hak cipta.",
            "- Efek suara yang disarankan jika diedit manual: whoosh 0,3 dtk di tiap transisi, pop ringan saat grafik muncul.", ""]
    out += ["## G. Panduan penyuntingan dan tempo", ""]
    avg = total / max(1, len(beats))
    out += [f"- {len(beats)} scene, rata-rata {avg:.1f} dtk per scene; gambar berubah minimal tiap 4-7 dtk, tiap kalimat narasi = satu scene.",
            "- 3 detik pertama harus memuat teks hook besar, maskot, dan gerak; jangan ada intro atau logo panjang.",
            "- Teks di layar singkat (maks 5 kata) karena detail ada di narasi dan caption; area bawah 340 piksel dan tepi kanan dibiarkan aman dari antarmuka Instagram.",
            "- Warna konsisten: putih, lavender, ungu #6B63C8, kuning stabilo #FFEB00, tinta #22222A. Latar berganti per scene agar ritme terasa.",
            "- Kecepatan bicara sekitar 2,5-3 kata per detik; jangan menambah kata tanpa memperpanjang scene.", ""]
    out += ["## Referensi dan status klaim", ""]
    for i, c in enumerate(claims, 1):
        out.append(f"{i}. {c.get('text', '')} (sumber: {c.get('source_name', '')}, {c.get('source_url', '')}) - klaim terverifikasi kode: kutipan persis ditemukan di halaman sumber.")
    interp = [b["say"] for b in beats if HEDGE.search(b["say"])]
    out += ["", "**Interpretasi, bukan fakta terukur** (ditandai kata seperti menurut / kemungkinan):"]
    out += [f"- {s}" for s in interp] or ["- (tidak ada)"]
    return "\n".join(out) + "\n"
