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

    def _apercu_signature(self, s, t):
        """État visible de la slide à l'instant `t` (composite constant par
        paliers). Si la signature ne bouge pas, l'image rendue est identique :
        on peut sauter le composite Pillow. Inclut la taille du canvas (un
        redimensionnement change l'image affichée)."""
        cap = composer.capture_active(s, t)
        return (
            self.current,
            id(cap) if cap is not None else None,
            composer.narration_active(s, t),
            tuple(id(a) for a in composer.annotations_actives(s, t)),
            tuple(id(tx) for tx in composer.textes_actifs(s, t)),
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
        try:
            img = composer.composer_scene(s, self.meta, t)
        except Exception as e:
            c.delete("all")
            c.create_text(10, 10, text=f"Aperçu indisponible :\n{e}",
                          fill="#e06060", anchor="nw")
            self._shot_box = None
            self._slide_disp = None
            self._apercu_sig = None  # retenter au prochain appel
            return
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
            return

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
        was_dragging = bool(self._free_drag or self._cap_drag or self._drag_anno)
        if self._free_drag:
            self._free_drag_move(ev)
            self._free_drag = None
        elif self._cap_drag:
            self._ap_move(ev)
            self._cap_drag = None
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
            elif (self._sel and self._sel["kind"] in ("arrow", "highlight")
                    and self._dans_slide(ev)):
                # Flèche/highlight sélectionné : déplaçable partout sur la slide.
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


