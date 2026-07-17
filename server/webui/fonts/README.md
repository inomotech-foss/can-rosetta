# Fonts

The dashboard uses **Manrope** (SIL Open Font License 1.1) as its brand
typeface. The OTFs are not bundled to keep the repo self-contained and
dependency-free — `tokens.css` declares a clean system fallback stack
(`system-ui`, `-apple-system`, `Segoe UI`, `Roboto`, …), so the UI renders
correctly without them.

For pixel-perfect fidelity with the design mockup, drop the OTFs here:

```
Manrope-Regular.otf
Manrope-Medium.otf
Manrope-SemiBold.otf
Manrope-Bold.otf
Manrope-ExtraBold.otf
```

The `@font-face` rules in `tokens.css` already point at these filenames.
Get them from https://github.com/sharanda-w/manrope (OFL).
