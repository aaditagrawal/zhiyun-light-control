// Keep extensionless TypeScript imports for Metro; make emitted npm declarations NodeNext-compatible.
for await (const path of new Bun.Glob('dist/*.d.ts').scan('.')) {
  const file = Bun.file(path);
  await Bun.write(path, (await file.text()).replace(/(from\s+['"])(\.\/[^'"]+)(['"])/g, '$1$2.js$3'));
}
