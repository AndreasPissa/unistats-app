import { copyFile, mkdir, cp, readFile, writeFile } from "node:fs/promises";
const target = new URL("../public/vendor/", import.meta.url);
await mkdir(target, { recursive: true });
await copyFile(
  new URL("../node_modules/chart.js/dist/chart.umd.js", import.meta.url),
  new URL("chart.umd.js", target),
);
await copyFile(
  new URL("../node_modules/chart.js/LICENSE.md", import.meta.url),
  new URL("Chart.js-LICENSE.md", target),
);
const leaflet = new URL("leaflet/", target);
await mkdir(leaflet, { recursive: true });
for (const file of ["leaflet.js", "leaflet.css"]) {
  await copyFile(
    new URL("../node_modules/leaflet/dist/" + file, import.meta.url),
    new URL(file, leaflet),
  );
}
await cp(
  new URL("../node_modules/leaflet/dist/images/", import.meta.url),
  new URL("images/", leaflet),
  { recursive: true },
);
await copyFile(
  new URL("../node_modules/leaflet/LICENSE", import.meta.url),
  new URL("LICENSE", leaflet),
);
for (const file of ["leaflet.markercluster.js", "MarkerCluster.css"]) {
  await copyFile(
    new URL(
      "../node_modules/leaflet.markercluster/dist/" + file,
      import.meta.url,
    ),
    new URL(file, leaflet),
  );
}
await copyFile(
  new URL(
    "../node_modules/leaflet.markercluster/MIT-LICENCE.txt",
    import.meta.url,
  ),
  new URL("MarkerCluster-LICENSE.txt", leaflet),
);
await copyFile(
  new URL(
    "../node_modules/leaflet.vectorgrid/dist/Leaflet.VectorGrid.bundled.min.js",
    import.meta.url,
  ),
  new URL("Leaflet.VectorGrid.bundled.min.js", leaflet),
);
const vectorReadme = await readFile(
  new URL("../node_modules/leaflet.vectorgrid/README.md", import.meta.url),
  "utf8",
);
await writeFile(
  new URL("VectorGrid-LICENSE.txt", leaflet),
  vectorReadme.split("## Legalese")[1].trim() + "\n",
);
console.log(
  "Vendored Chart.js and Leaflet locally. Map tiles load from OpenStreetMap only when the map is opened.",
);
