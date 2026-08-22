// SnapshotImage object-fit by node type (2026-08-22).
// Wall mode (4K 牆面模式) rendered webcam frames with object-cover, cropping a
// non-16:9 webcam into a bar. Webcam frames should show the FULL image
// (object-contain); edge cameras keep object-cover (fixed 16:9, fill the tile).
module.exports = [
  {
    name: 'wall-webcam-fit components.jsx: webcam=object-contain, camera=object-cover',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      const mk = (over) => Object.assign({ id: 'n', status: 'online', upload: 2, snapshotTimestamp: 42 }, over);
      const render = (node) => ReactDOM.flushSync(() => root.render(React.createElement(SnapshotImage, { node })));

      // --- webcam: full frame, letterboxed (object-contain), never cropped ---
      render(mk({ id: 'webcam_1', type: 'webcam' }));
      await settle();
      let img = container.querySelector('img');
      A('webcam live frame renders an <img>', !!img, container.innerHTML.slice(0, 200));
      A('webcam frame uses object-contain (full image, not cropped into a bar)',
        !!img && img.className.indexOf('object-contain') !== -1 && img.className.indexOf('object-cover') === -1,
        img && img.className);

      // --- edge camera: unchanged, fills the tile (object-cover) ---
      ReactDOM.flushSync(() => root.render(null));
      render(mk({ id: 'CAM-1', type: 'camera' }));
      await settle();
      img = container.querySelector('img');
      A('edge camera live frame renders an <img>', !!img);
      A('edge camera frame keeps object-cover (fills the tile)',
        !!img && img.className.indexOf('object-cover') !== -1 && img.className.indexOf('object-contain') === -1,
        img && img.className);
    `,
  },
];
