# Unicode NSIS ZIP extractor

The fork's Windows installer uses the x86 Unicode `nsisunz.dll` from
electron-builder's nsis-resources-3.4.1 release. The NSIS compiler still
comes from the current toolset. Its 1.2.1 bundle ships the same ANSI DLL
under both the ANSI and Unicode paths. [Release archive](https://github.com/electron-userland/electron-builder-binaries/releases/tag/nsis-resources-3.4.1)

The DLL matches the original NSISunzU distribution of Saivert's plugin with
Unicode support by Gringoloco. The installer places the license in
`resources/LICENSE.nsisunz.txt`. [Plugin source and license](https://nsis.sourceforge.io/Nsisunz_plug-in)

| Artifact | SHA-256 |
| --- | --- |
| `nsis-resources-3.4.1.7z` | `593a9a92ef958321293ac6a2ee61e64bf1bd543142a5bd6b3d310709cc924103` |
| `plugins/x86-unicode/nsisunz.dll` | `c31b590cba443de87f0f4a81712f0883ac3b506f3868759d918d9a81f84ea922` |
