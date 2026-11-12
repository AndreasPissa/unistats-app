// Original UniStats cartography for the OpenStreetMap Shortbread v1 schema.
// SVG keeps linework sharp when zooming beyond the source's native zoom 14.
export function blueprintBasemap() {
  const L = window.L;
  const light = document.documentElement.dataset.theme === "light";
  const tone = (dark, pale) => (light ? pale : dark);
  const line = (color, weight, opacity = 1) => ({
    color,
    weight,
    opacity,
    fill: false,
    lineCap: "round",
    lineJoin: "round",
  });
  const water = {
    stroke: false,
    fill: true,
    fillColor: tone("#101613", "#f3f6f4"),
    fillOpacity: 1,
  };
  const styles = {
    ocean: water,
    water_polygons: water,
    water_lines: line(tone("#2e473a", "#acc2b2"), 0.6, 0.7),
    boundaries: (properties) =>
      properties.maritime
        ? []
        : {
            ...line(tone("#576d60", "#6f8e79"), 0.65, 0.45),
            dashArray: "3 5",
          },
    buildings: {
      ...line(tone("#486052", "#829e8b"), 0.45, 0.55),
      fill: true,
      fillColor: tone("#233b2c", "#d3e2d8"),
      fillOpacity: 0.32,
    },
    street_polygons: {
      stroke: false,
      fill: true,
      fillColor: tone("#475f50", "#bfd3c6"),
      fillOpacity: 0.28,
    },
    streets: (properties, zoom) => {
      const major = ["motorway", "trunk", "primary"].includes(properties.kind);
      const secondary = ["secondary", "tertiary"].includes(properties.kind);
      const path = ["path", "footway", "steps", "track", "cycleway"].includes(
        properties.kind,
      );
      if (path && zoom < 14) return [];
      return {
        ...line(
          major
            ? tone("#b3c6b9", "#49725a")
            : secondary
              ? tone("#91a99b", "#6b8f78")
              : tone("#728a7d", "#9bb5a4"),
          major ? 1.05 : secondary ? 0.8 : 0.55,
          properties.tunnel ? 0.3 : major ? 0.8 : path ? 0.32 : 0.56,
        ),
        ...(properties.rail ? { dashArray: "3 3", opacity: 0.4 } : {}),
      };
    },
    pier_lines: line(tone("#779785", "#6d917b"), 0.7, 0.6),
    pier_polygons: {
      ...line(tone("#779785", "#6d917b"), 0.6, 0.6),
      fill: true,
      fillColor: tone("#1b2b22", "#d9e7df"),
      fillOpacity: 1,
    },
    dam_lines: line(tone("#779785", "#6d917b"), 0.7, 0.6),
  };

  const BlueprintGrid = L.VectorGrid.Protobuf.extend({
    async _getVectorTilePromise(coords) {
      try {
        const tile =
          await L.VectorGrid.Protobuf.prototype._getVectorTilePromise.call(
            this,
            coords,
          );
        // VectorGrid represents an HTTP failure as an array instead of a layer map.
        if (Array.isArray(tile.layers)) throw new Error("Map tile unavailable");
        // Draw in deliberate order and omit POIs, labels and unknown future layers.
        tile.layers = Object.fromEntries(
          Object.keys(styles)
            .filter((name) => tile.layers[name])
            .map((name) => [name, tile.layers[name]]),
        );
        return tile;
      } catch (error) {
        this.fire("tileerror", { error, coords });
        return { layers: {} };
      }
    },
  });

  return new BlueprintGrid(
    "https://vector.openstreetmap.org/shortbread_v1/{z}/{x}/{y}.mvt",
    {
      rendererFactory: L.svg.tile,
      vectorTileLayerStyles: styles,
      className: "blueprint-tiles",
      maxNativeZoom: 14,
      maxZoom: 19,
      noWrap: true,
      keepBuffer: 1,
      updateWhenIdle: true,
      fetchOptions: { credentials: "omit" },
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>',
    },
  );
}
