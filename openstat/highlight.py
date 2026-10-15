"""Small, dependency-free Stata-style editor highlighter for Qt."""

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat


class StataHighlighter(QSyntaxHighlighter):
    """Highlight syntax without claiming to parse the language."""

    def __init__(self, document):
        super().__init__(document)
        self.rules = []
        for pattern, color, bold in [
            (r"\b(clear|set|use|save|import|export|generate|gen|replace|egen|drop|keep|rename|sort|gsort|by|bysort|foreach|forvalues|while|if|else|local|global|scalar|matrix|program|end|capture|quietly|noisily|regress|reg|summarize|sum|describe|list|tabulate|tab|count|display|di|assert|preserve|restore|merge|append|reshape|collapse|encode|decode|label|recode|do|run|cd|pwd|log|ssc|help|version|mata|exit|return|ereturn)\b", "#145f84", True),
            (r"\b[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\b", "#8856a7", False),
            (r"\$[A-Za-z_][A-Za-z_0-9]*|`[^']*'", "#ad660e", False),
            (r'"[^"\n]*"', "#92701e", False),
            (r"//[^\n]*|^\s*\*[^\n]*", "#64816c", False),
        ]:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if bold:
                fmt.setFontWeight(QFont.Weight.DemiBold)
            self.rules.append((QRegularExpression(pattern), fmt))
        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(QColor("#64816c"))

    def highlightBlock(self, text):
        for expression, fmt in self.rules:
            matches = expression.globalMatch(text)
            while matches.hasNext():
                match = matches.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)
        self.setCurrentBlockState(0)
        start = 0 if self.previousBlockState() == 1 else text.find("/*")
        while start >= 0:
            end = text.find("*/", start + (0 if self.previousBlockState() == 1 and start == 0 else 2))
            if end < 0:
                self.setFormat(start, len(text) - start, self.comment_format)
                self.setCurrentBlockState(1)
                break
            self.setFormat(start, end + 2 - start, self.comment_format)
            start = text.find("/*", end + 2)
