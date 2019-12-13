import io
import functools
from fontTools.ttLib import TTFont
import uharfbuzz as hb


class GlyphInfo:

    def __init__(self, gid, name, cluster, dx, dy, ax, ay):
        self.gid = gid
        self.name = name
        self.cluster = cluster
        self.dx = dx
        self.dy = dy
        self.ax = ax
        self.ay = ay

    def __repr__(self):
        args = (f"{a}={repr(getattr(self, a))}"
                    for a in ["gid", "name", "cluster", "dx", "dy", "ax", "ay"])
        return f"{self.__class__.__name__}({', '.join(args)})"


charToGlyphIDBias = 0x80000000


#
# To generalize, HB needs
# - a callback to map a character to a glyph ID
# - a callback to get the advance width for a glyph ID
# - a callback to get the advance height for a glyph ID
# - either data for a minimal ttf, or a callback getting individual table data
#   (the latter is broken in current uharfbuzz)
# To make our shaper work, we need
# - to provide a glyph order so we can map glyph IDs to glyph names
# - apart from providing a glyph order, we want our callbacks to deal
#   with glyph names, not glyph IDs.
#


def _getGlyphIDFunc(font, char, shaper):
    if char >= charToGlyphIDBias:
        return char - charToGlyphIDBias
    glyphName = shaper.getGlyphNameFromCodePoint(char)
    if glyphName is None:
        return 0  # .notdef
    glyphID = shaper.getGlyphID(glyphName, 0)
    return glyphID


def _getAdvanceWidthFunc(font, glyphID, shaper):
    glyphName = shaper.glyphOrder[glyphID]
    width = shaper.getAdvanceWidth(glyphName)
    return width


class HBShape:

    @classmethod
    def fromPath(cls, path, **kwargs):
        with open(path, "rb") as f:
            fontData = f.read()
        return cls(fontData, **kwargs)

    def __init__(self, fontData, *, fontNumber=0,
                 getGlyphNameFromCodePoint=None,
                 getAdvanceWidth=None, ttFont=None):
        self._fontData = fontData
        self._fontNumber = fontNumber
        self.face = hb.Face(fontData, fontNumber)
        self.font = hb.Font(self.face)
        self._funcs = hb.FontFuncs.create()

        if ttFont is None:
            f = io.BytesIO(self._fontData)
            ttFont = TTFont(f, fontNumber=self._fontNumber, lazy=True)
        self._ttFont = ttFont
        self.glyphOrder = ttFont.getGlyphOrder()

        if getGlyphNameFromCodePoint is None:
            def _getGlyphNameFromCodePoint(cmap, codePoint):
                return cmap.get(codePoint)
            getGlyphNameFromCodePoint = functools.partial(_getGlyphNameFromCodePoint, self._ttFont.getBestCmap())
        self.getGlyphNameFromCodePoint = getGlyphNameFromCodePoint

        self._funcs.set_nominal_glyph_func(_getGlyphIDFunc, self)

        if getAdvanceWidth is None:
            # TODO: this is wrong for var fonts, we should not set a width func at all
            # TODO: the problem seems to be we need to set all funcs, or the ones we
            # don't set will misbehave. We currently go through hoops to support glyph
            # name input, but if that is not needed we can skip the advance with func,
            # too.
            def _getAdvanceWidth(hmtx, glyphName):
                return hmtx[glyphName][0]
            getAdvanceWidth = functools.partial(_getAdvanceWidth, self._ttFont["hmtx"])
        self.getAdvanceWidth = getAdvanceWidth

        self._funcs.set_glyph_h_advance_func(_getAdvanceWidthFunc, self)

    def getFeatures(self, tag):
        return hb.ot_layout_language_get_feature_tags(self.face, tag)

    def getLanguages(self, tag):
        return hb.ot_layout_script_get_language_tags(self.face, tag)

    def getScripts(self, tag):
        return hb.ot_layout_table_get_script_tags(self.face, tag)

    def getGlyphID(self, glyphName, default=0):
        try:
            return self._ttFont.getGlyphID(glyphName)
        except KeyError:
            return default

    def shape(self, text, *, features=None, variations=None,
              direction=None, language=None, script=None):
        if features is None:
            features = {}
        if variations is None:
            variations = {}

        glyphOrder = self.glyphOrder

        self.font.scale = (self.face.upem, self.face.upem)
        self.font.set_variations(variations)

        hb.ot_font_set_funcs(self.font)

        if self._funcs is not None:
            self.font.funcs = self._funcs

        buf = hb.Buffer.create()
        if isinstance(text, str):
            buf.add_str(str(text))  # add_str() does not accept str subclasses
        else:
            codePoints = []
            for char in text:
                if isinstance(char, str):
                    # It's a glyph name
                    codePoint = self.getGlyphID(char, 0) + charToGlyphIDBias
                else:
                    codePoint = char
                codePoints.append(codePoint)
            buf.add_codepoints(codePoints)
        buf.guess_segment_properties()

        if direction is not None:
            buf.direction = direction
        if language is not None:
            buf.language = language
        if script is not None:
            buf.script = script

        hb.shape(self.font, buf, features)

        infos = []
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            infos.append(GlyphInfo(info.codepoint, glyphOrder[info.codepoint], info.cluster, *pos.position))

        return infos


if __name__ == "__main__":
    import sys
    shaper = Shaper.fromPath(sys.argv[1])
    txt = sys.argv[2]
    for g in shaper.shape(txt):
        print(g)
