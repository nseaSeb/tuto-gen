"""Aperçu interactif : rendu de la slide + interactions souris."""

from __future__ import annotations

import queue
import subprocess
import tempfile
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .. import composer, config, paquet, settings
from ..cli import _ouvrir, _slug, construire
from .common import (
    VERT, TL_LEFT, TL_TOP, TL_ROW_H, TL_GAP, TL_HANDLE, TL_MIN_BODY,
    SETTINGS_W, LIST_W, COUL, PRESETS_TEXTE, ROLE_LABEL,
    _to_float, _lighten, _parse_fin, _Q,
)


class ApercuMixin:
    # =========================================================== APERÇU
    def _plan_apercu(self, immediate: bool = False):
        # Toute (re)planification vient d'une édition/scrub → le contenu a pu
        # changer sans que l'« état visible » (signature) bouge : on invalide
        # pour forcer un vrai rendu (le saut n'est utile qu'en lecture).
        self._apercu_sig = None
        self._base_cache = None  # purge le cache d'image de base (zoom)
        if self._apercu_job:
            self.root.after_cancel(self._apercu_job)
            self._apercu_job = None
        if immediate:
            self._draw_apercu()
        else:
            self._apercu_job = self.root.after(180, self._draw_apercu)

    def _ghost_box(self, box, couleur="#46e08a"):
        """Trace un rectangle « fantôme » (destination du drag) sur l'aperçu."""
        if not self._slide_disp:
            return
        ix, iy, ratio = self._slide_disp
        x, y, bw, bh = box
        self.apercu_canvas.create_rectangle(
            ix + x * ratio, iy + y * ratio,
            ix + (x + bw) * ratio, iy + (y + bh) * ratio,
            outline=couleur, width=2, dash=(4, 3), tags="dragghost")

    def _draw_ghost(self):
        """Aperçu léger pendant un drag : on déplace un repère (sans
        recomposer toute la slide avec Pillow). La composition exacte est
        refaite au relâché (`_ap_up`)."""
        c = self.apercu_canvas
        c.delete("dragghost")
        if self.current is None or not self._slide_disp:
            return
        s = self.scenes[self.current]
        if self._free_drag:
            kind, idx = self._free_drag["kind"], self._free_drag["idx"]
            box = None
            if kind in ("titre", "sous_titre", "logo"):
                box = (composer.zones_titre(s, self.meta) or {}).get(kind)
            elif kind == "texte":
                for i, b in composer.zones_textes(s, self.meta):
                    if i == idx:
                        box = b
                        break
            if box:
                self._ghost_box(box)
        elif self._cap_drag:
            zs = composer.zone_screenshot(s, self.meta, self._preview_t)
            if zs:
                self._ghost_box(zs)
        elif self._drag_anno and self._shot_box:
            idx = self._drag_anno["idx"]
            if idx >= len(s.annotations):
                return
            a = s.annotations[idx]
            bx, by, bw, bh = self._shot_box

            def _px(p):
                return (bx + bw * p[0] / 100.0, by + bh * p[1] / 100.0)
            if a.type == "arrow" and a.de and a.vers:
                x0, y0 = _px(a.de)
                x1, y1 = _px(a.vers)
                c.create_line(x0, y0, x1, y1, fill="#FF6B35", width=3,
                              arrow="last", tags="dragghost")
            elif a.type == "highlight" and a.zone:
                x0, y0 = _px((a.zone[0], a.zone[1]))
                x1, y1 = _px((a.zone[2], a.zone[3]))
                c.create_rectangle(x0, y0, x1, y1, outline="#FFD166", width=2,
                                   dash=(4, 3), tags="dragghost")

    def _zoom_box(self):
        """Repère (origine_x, origine_y, largeur, hauteur) en pixels-canvas où
        s'expriment les % de la zone du zoom sélectionné : la capture
        (cible « capture ») ou la slide entière (cible « slide »)."""
        z = None
        if self._sel and self._sel.get("kind") == "zoom" and self.current is not None:
            s = self.scenes[self.current]
            if self._sel["idx"] < len(s.zooms):
                z = s.zooms[self._sel["idx"]]
        if z is not None and getattr(z, "cible", "slide") == "capture" \
                and self._shot_box:
            return self._shot_box
        if self._slide_disp:
            ix, iy, ratio = self._slide_disp
            W, H = self.meta.resolution
            return (ix, iy, W * ratio, H * ratio)
        return None

    def _zoom_overlay(self, z):
        """Trace la zone cible d'un zoom (rectangle pointillé) dans son repère."""
        box = self._zoom_box()
        if not box:
            return
        c = self.apercu_canvas
        c.delete("zoomzone")
        bx, by, bw, bh = box
        zx1, zy1, zx2, zy2 = z.zone
        x1 = bx + zx1 / 100.0 * bw
        y1 = by + zy1 / 100.0 * bh
        x2 = bx + zx2 / 100.0 * bw
        y2 = by + zy2 / 100.0 * bh
        cible = "capture" if getattr(z, "cible", "slide") == "capture" else "slide"
        c.create_rectangle(x1, y1, x2, y2, outline="#46e0e0", width=2,
                           dash=(5, 4), tags="zoomzone")
        c.create_text(x1 + 4, y1 + 4, text=f"🔍 zoom ({cible})", anchor="nw",
                      fill="#46e0e0", font=("Helvetica", 9, "bold"),
                      tags="zoomzone")

    def _apercu_signature(self, s, t):
        """État visible de la slide à l'instant `t` (composite constant par
        paliers). Si la signature ne bouge pas, l'image rendue est identique :
        on peut sauter le composite Pillow. Inclut la taille du canvas (un
        redimensionnement change l'image affichée)."""
        cap = composer.capture_active(s, t)
        # État du zoom : version LÉGÈRE (avancement seul, sans recharger la
        # capture). Le cadrage exact dépend en plus de la capture active, déjà
        # présente via `id(cap)` ci-dessus — inutile (et coûteux) d'appeler
        # `zoom_transform`/`zone_screenshot` ici, à chaque frame de lecture.
        # L'effet est TOUJOURS affiché (lecture comme scrub), même bloc zoom
        # sélectionné ; la zone éditable n'apparaît qu'en vue pleine (p == 0).
        zoom_sig = None
        z, p = composer._zoom_actif(s, t, self._tl_duree())
        if z is not None and p > 0.0:
            zoom_sig = (id(z), round(p, 4), getattr(z, "cible", "slide"))
        sel_zoom = (self._sel.get("idx")
                    if self._sel and self._sel.get("kind") == "zoom" else None)
        return (
            self.current,
            id(cap) if cap is not None else None,
            composer.narration_active(s, t),
            tuple(id(a) for a in composer.annotations_actives(s, t)),
            tuple(id(tx) for tx in composer.textes_actifs(s, t)),
            zoom_sig,
            sel_zoom,
            self.apercu_canvas.winfo_width(),
            self.apercu_canvas.winfo_height(),
        )

    def _draw_apercu(self):
        self._apercu_job = None
        c = self.apercu_canvas
        if self.current is None:
            c.delete("all")
            self._shot_box = None
            self._slide_disp = None
            self._apercu_sig = None
            return
        s = self.scenes[self.current]
        t = self._preview_t
        if hasattr(self, "lbl_apercu_t"):
            self.lbl_apercu_t.config(text=f"t = {t:.1f}s")
        # Saut du rendu si l'état visible est inchangé (lecture : évite un
        # composite Pillow + resize + PhotoImage par frame). Les éditions
        # invalident `_apercu_sig` via `_plan_apercu`, donc jamais d'image
        # périmée à l'édition.
        sig = self._apercu_signature(s, t)
        if sig == self._apercu_sig and self._tk_img is not None:
            return
        self._apercu_sig = sig
        # Image de base (avant zoom) mise en cache : pendant un zoom seul le
        # cadrage change d'une frame à l'autre — inutile de recomposer toute la
        # slide. La clé exclut le cadrage du zoom ; le cache est purgé à chaque
        # édition par `_plan_apercu`.
        base_key = sig[:5]
        try:
            if self._base_cache and self._base_cache[0] == base_key:
                img = self._base_cache[1]
            else:
                img = composer.composer_scene(s, self.meta, t)
                self._base_cache = (base_key, img)
        except Exception as e:
            c.delete("all")
            c.create_text(10, 10, text=f"Aperçu indisponible :\n{e}",
                          fill="#e06060", anchor="nw")
            self._shot_box = None
            self._slide_disp = None
            self._apercu_sig = None  # retenter au prochain appel
            self._base_cache = None
            return
        # Zoom (caméra) : l'effet est toujours appliqué quand il est actif (p>0),
        # y compris quand le bloc zoom est sélectionné. La zone éditable
        # n'apparaît qu'en vue pleine (p == 0, donc tr is None), plus bas.
        tr = None
        try:
            tr = composer.zoom_transform(s, self.meta, t, self._tl_duree())
            if tr is not None:
                img = composer.appliquer_zoom(img, *tr)
        except Exception:
            tr = None  # un zoom raté ne doit pas figer l'aperçu/la lecture

        cw = max(320, c.winfo_width() - 4)
        ch = max(180, c.winfo_height() - 4)
        ratio = min(cw / img.width, ch / img.height, 1.0)
        dw = max(1, int(img.width * ratio))
        dh = max(1, int(img.height * ratio))
        disp = img.resize((dw, dh), Image.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(disp)
        ix = max(0, (c.winfo_width() - dw) // 2)
        iy = max(0, (c.winfo_height() - dh) // 2)
        c.delete("all")
        c.create_image(ix, iy, image=self._tk_img, anchor="nw")
        # Vue zoomée (lecture/scrub) : pas d'édition au pixel près → on n'arme
        # pas le mapping de drag ni les repères.
        if tr is not None:
            self._slide_disp = None
            self._shot_box = None
            return
        self._slide_disp = (ix, iy, ratio)

        # Repères pointillés autour du titre / sous-titre (logo sur les slides
        # titre), déplaçables au drag — sur tous les types de slide.
        for box in (composer.zones_titre(s, self.meta) or {}).values():
            x, y, bw, bh = box
            c.create_rectangle(ix + x * ratio, iy + y * ratio,
                               ix + (x + bw) * ratio, iy + (y + bh) * ratio,
                               outline="#46a06a", width=1, dash=(4, 4))

        if s.type == "title":
            self._shot_box = None
        else:
            zs = composer.zone_screenshot(s, self.meta, t)
            if zs:
                ox, oy, sw, sh = zs
                bx = ix + int(ox * ratio)
                by = iy + int(oy * ratio)
                bw = int(sw * ratio)
                bh = int(sh * ratio)
                self._shot_box = (bx, by, bw, bh)
                if self._sel and self._sel["kind"] in ("arrow", "highlight"):
                    c.create_rectangle(bx, by, bx + bw, by + bh,
                                       outline="#FFD166", width=1, dash=(5, 4))
            else:
                self._shot_box = None

        # Zone cible du zoom sélectionné (déplaçable au drag) — tracée en
        # dernier, après `_shot_box` (nécessaire pour la cible « capture »). On
        # n'arrive ici qu'en vue pleine (tr is None) : l'effet n'est pas appliqué
        # à cet instant, la zone est donc éditable directement.
        if (self._sel and self._sel.get("kind") == "zoom"
                and self._sel["idx"] < len(s.zooms)):
            self._zoom_overlay(s.zooms[self._sel["idx"]])

    def _bornes_pct(self) -> tuple[float, float, float, float]:
        """Bornes (xmin, xmax, ymin, ymax) en % de la boîte capture
        correspondant aux bords de la slide. Permet de placer une flèche /
        un highlight hors de l'image (mais dans la slide)."""
        if not (self._slide_disp and self._shot_box):
            return (0.0, 100.0, 0.0, 100.0)
        ix, iy, ratio = self._slide_disp
        bx, by, bw, bh = self._shot_box
        if bw <= 0 or bh <= 0 or ratio <= 0:
            return (0.0, 100.0, 0.0, 100.0)
        sw, sh = self.meta.resolution
        dw, dh = sw * ratio, sh * ratio
        xmin = (ix - bx) / bw * 100
        xmax = (ix + dw - bx) / bw * 100
        ymin = (iy - by) / bh * 100
        ymax = (iy + dh - by) / bh * 100
        return (xmin, xmax, ymin, ymax)

    def _canvas_pct(self, cx: int, cy: int) -> tuple[float, float] | None:
        if not self._shot_box:
            return None
        bx, by, bw, bh = self._shot_box
        if bw <= 0 or bh <= 0:
            return None
        xmin, xmax, ymin, ymax = self._bornes_pct()
        return (round(max(xmin, min(xmax, (cx - bx) / bw * 100)), 1),
                round(max(ymin, min(ymax, (cy - by) / bh * 100)), 1))

    def _ap_down(self, ev):
        if self._playing or self._play_intent:
            self._play_stop()
        if self.current is None:
            return
        s = self.scenes[self.current]
        # Priorité : déplacement libre (texte / logo / titre / sous-titre).
        hit = self._hit_libre(ev, s)
        if hit:
            self._free_drag = hit
            self._free_drag_move(ev)
            return
        if not self._sel:
            return
        k, i = self._sel["kind"], self._sel["idx"]
        # Capture sélectionnée : on la déplace (décalage) à la souris.
        if k == "capture" and i < len(s.captures) and self._slide_disp:
            cap = s.captures[i]
            self._cap_drag = {"idx": i, "sx": ev.x, "sy": ev.y,
                              "ox": cap.decalage_x, "oy": cap.decalage_y}
            return
        # Zone de zoom sélectionnée : translation à la souris (% de slide).
        if k == "zoom" and i < len(s.zooms) and self._slide_disp:
            self._drag_zoom = {"idx": i, "sx": ev.x, "sy": ev.y,
                               "orig_zone": s.zooms[i].zone}
            return
        # Sinon : translation d'une flèche / highlight sélectionné (capture).
        if k not in ("arrow", "highlight") or i >= len(s.annotations):
            return
        a = s.annotations[i]
        pct = self._canvas_pct(ev.x, ev.y)
        if pct is None:
            return
        self._drag_anno = {
            "start": pct, "idx": i, "type": a.type,
            "orig_de": a.de, "orig_vers": a.vers, "orig_zone": a.zone,
        }
        self._draw_ghost()

    @staticmethod
    def _delta_borne(delta, valeurs, mini=0.0, maxi=100.0):
        """Borne un déplacement pour que toutes les `valeurs` restent dans
        l'intervalle [mini, maxi]."""
        lo = mini - min(valeurs)
        hi = maxi - max(valeurs)
        return max(lo, min(hi, delta))

    def _ap_move(self, ev):
        if self._free_drag and self.current is not None:
            self._free_drag_move(ev)
            return
        if self._cap_drag and self.current is not None and self._slide_disp:
            ix, iy, ratio = self._slide_disp
            w, h = self.meta.resolution
            cd = self._cap_drag
            s = self.scenes[self.current]
            if cd["idx"] < len(s.captures) and ratio > 0:
                cap = s.captures[cd["idx"]]
                cap.decalage_x = round(cd["ox"] + (ev.x - cd["sx"]) / ratio / w * 100, 1)
                cap.decalage_y = round(cd["oy"] + (ev.y - cd["sy"]) / ratio / h * 100, 1)
                self._sync_cap_pos(cap)
                self._draw_ghost()
            return
        if self._drag_zoom and self.current is not None:
            dz = self._drag_zoom
            s = self.scenes[self.current]
            box = self._zoom_box()
            if box and dz["idx"] < len(s.zooms):
                _bx, _by, bw, bh = box
                z = s.zooms[dz["idx"]]
                if bw > 0 and bh > 0:
                    ddx = (ev.x - dz["sx"]) / bw * 100
                    ddy = (ev.y - dz["sy"]) / bh * 100
                    x0, y0, x1, y1 = dz["orig_zone"]
                    dx = self._delta_borne(ddx, (x0, x1), 0.0, 100.0)
                    dy = self._delta_borne(ddy, (y0, y1), 0.0, 100.0)
                    z.zone = (round(x0 + dx, 1), round(y0 + dy, 1),
                              round(x1 + dx, 1), round(y1 + dy, 1))
                    self._sync_zoom_zone(z)
                    self._zoom_overlay(z)
            return
        if not self._drag_anno or self.current is None:
            return
        s = self.scenes[self.current]
        a = s.annotations[self._drag_anno["idx"]]
        pct = self._canvas_pct(ev.x, ev.y)
        if pct is None:
            return
        sp = self._drag_anno["start"]
        ddx, ddy = pct[0] - sp[0], pct[1] - sp[1]
        xmin, xmax, ymin, ymax = self._bornes_pct()
        d = self._drag_anno
        if d["type"] == "arrow" and d["orig_de"] and d["orig_vers"]:
            (x0, y0), (x1, y1) = d["orig_de"], d["orig_vers"]
            dx = self._delta_borne(ddx, (x0, x1), xmin, xmax)
            dy = self._delta_borne(ddy, (y0, y1), ymin, ymax)
            a.de = (round(x0 + dx, 1), round(y0 + dy, 1))
            a.vers = (round(x1 + dx, 1), round(y1 + dy, 1))
        elif d["type"] == "highlight" and d["orig_zone"]:
            x0, y0, x1, y1 = d["orig_zone"]
            dx = self._delta_borne(ddx, (x0, x1), xmin, xmax)
            dy = self._delta_borne(ddy, (y0, y1), ymin, ymax)
            a.zone = (round(x0 + dx, 1), round(y0 + dy, 1),
                      round(x1 + dx, 1), round(y1 + dy, 1))
        self._sync_anno(a)
        self._draw_ghost()

    def _ap_up(self, ev):
        was_dragging = bool(self._free_drag or self._cap_drag
                            or self._drag_anno or self._drag_zoom)
        if self._free_drag:
            self._free_drag_move(ev)
            self._free_drag = None
        elif self._cap_drag:
            self._ap_move(ev)
            self._cap_drag = None
        elif self._drag_zoom:
            self._ap_move(ev)
            self._drag_zoom = None
        else:
            self._ap_move(ev)
            self._drag_anno = None
        if was_dragging:
            # Drag terminé : on efface le fantôme et on recompose l'image exacte.
            self.apercu_canvas.delete("dragghost")
            self._plan_apercu(immediate=True)

    def _sync_cap_pos(self, cap: config.Capture):
        v = self._cap_pos_vars
        if not v:
            return
        self._chargement = True
        try:
            v["x"].set(f"{cap.decalage_x:.0f}"); v["y"].set(f"{cap.decalage_y:.0f}")
        finally:
            self._chargement = False

    def _sync_zoom_zone(self, z):
        """Reporte la zone (drag) dans les champs du panneau Zoom."""
        v = getattr(self, "_zoom_vars", None)
        if not v:
            return
        self._chargement = True
        try:
            v["x1"].set(f"{z.zone[0]:.1f}"); v["y1"].set(f"{z.zone[1]:.1f}")
            v["x2"].set(f"{z.zone[2]:.1f}"); v["y2"].set(f"{z.zone[3]:.1f}")
        finally:
            self._chargement = False

    # ── Drag du titre / sous-titre sur les slides "title" ─────────────────
    def _move_cursor(self) -> str:
        """Nom du curseur « déplacer » (flèche 4 directions) supporté par Tk."""
        if self._move_cursor_name is None:
            self._move_cursor_name = "hand2"  # repli sûr
            for nom in ("fleur", "size", "hand2"):
                try:
                    self.apercu_canvas.config(cursor=nom)
                    self._move_cursor_name = nom
                    break
                except tk.TclError:
                    continue
            self.apercu_canvas.config(cursor="crosshair")
        return self._move_cursor_name

    def _ap_hover(self, ev):
        """Curseur « déplacer » au survol d'un objet déplaçable, sinon viseur."""
        cursor = "crosshair"
        if self.current is not None:
            s = self.scenes[self.current]
            if self._hit_libre(ev, s):
                cursor = self._move_cursor()
            elif (self._sel and self._sel["kind"] in ("arrow", "highlight", "zoom")
                    and self._dans_slide(ev)):
                # Flèche/highlight/zoom sélectionné : déplaçable sur la slide.
                cursor = self._move_cursor()
        if self.apercu_canvas["cursor"] != cursor:
            self.apercu_canvas.config(cursor=cursor)

    def _dans_slide(self, ev) -> bool:
        if not self._slide_disp:
            return False
        ix, iy, ratio = self._slide_disp
        sw, sh = self.meta.resolution
        return ix <= ev.x <= ix + sw * ratio and iy <= ev.y <= iy + sh * ratio

    def _hit_libre(self, ev, s: config.Scene) -> dict | None:
        """Élément déplaçable sous le curseur : texte / logo / titre / sous-titre.

        Renvoie {"kind", "idx"} ou None. Les textes (au-dessus) priment, puis
        sur les slides titre : sous-titre, titre, logo.
        """
        if not self._slide_disp:
            return None
        ix, iy, ratio = self._slide_disp
        pad = 8

        def _dans(box):
            x, y, bw, bh = box
            bx, by = ix + x * ratio, iy + y * ratio
            return (bx - pad <= ev.x <= bx + bw * ratio + pad
                    and by - pad <= ev.y <= by + bh * ratio + pad)

        for i, box in reversed(composer.zones_textes(s, self.meta)):
            if _dans(box):
                return {"kind": "texte", "idx": i}
        # Titre / sous-titre déplaçables sur tous les types ; le logo seulement
        # sur les slides titre (zones_titre ne l'expose pas ailleurs).
        zones = composer.zones_titre(s, self.meta) or {}
        for cle in ("sous_titre", "titre", "logo"):
            box = zones.get(cle)
            if box and _dans(box):
                return {"kind": cle, "idx": 0}
        return None

    def _free_drag_move(self, ev):
        if not self._slide_disp or self.current is None or not self._free_drag:
            return
        ix, iy, ratio = self._slide_disp
        if ratio <= 0:
            return
        w, h = self.meta.resolution
        cx = round(max(0.0, min(100.0, (ev.x - ix) / ratio / w * 100)), 1)
        cy = round(max(0.0, min(100.0, (ev.y - iy) / ratio / h * 100)), 1)
        s = self.scenes[self.current]
        kind, idx = self._free_drag["kind"], self._free_drag["idx"]
        if kind == "titre":
            s.titre_x, s.titre_y = cx, cy
            self._sync_title_pos(s)
        elif kind == "sous_titre":
            s.sous_titre_x, s.sous_titre_y = cx, cy
            self._sync_title_pos(s)
        elif kind == "logo":
            s.logo_x, s.logo_y = cx, cy
        elif kind == "texte" and idx < len(s.textes):
            s.textes[idx].x, s.textes[idx].y = cx, cy
            self._sync_texte_pos(s.textes[idx])
        self._draw_ghost()

    def _sync_texte_pos(self, tx: config.TexteLibre):
        v = self._texte_pos_vars
        if not v:
            return
        self._chargement = True
        try:
            v["x"].set(f"{tx.x:.0f}"); v["y"].set(f"{tx.y:.0f}")
        finally:
            self._chargement = False

    def _sync_title_pos(self, s: config.Scene):
        v = self._title_pos_vars
        if not v:
            return
        self._chargement = True
        try:
            v["tx"].set(f"{s.titre_x:.0f}"); v["ty"].set(f"{s.titre_y:.0f}")
            v["sx"].set(f"{s.sous_titre_x:.0f}"); v["sy"].set(f"{s.sous_titre_y:.0f}")
        finally:
            self._chargement = False

    def _sync_anno(self, a: config.Annotation):
        self._chargement = True
        try:
            if a.type == "arrow" and self._arrow_vars:
                v = self._arrow_vars
                if a.de:
                    v["dx"].set(f"{a.de[0]:.1f}"); v["dy"].set(f"{a.de[1]:.1f}")
                if a.vers:
                    v["vx"].set(f"{a.vers[0]:.1f}"); v["vy"].set(f"{a.vers[1]:.1f}")
            elif a.type == "highlight" and self._hl_vars and a.zone:
                v = self._hl_vars
                v["x1"].set(f"{a.zone[0]:.1f}"); v["y1"].set(f"{a.zone[1]:.1f}")
                v["x2"].set(f"{a.zone[2]:.1f}"); v["y2"].set(f"{a.zone[3]:.1f}")
        finally:
            self._chargement = False


