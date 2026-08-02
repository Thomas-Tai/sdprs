// delta-audit remediation suite.
// Finding: NEW-UX-008 — three fixed banners (dataWarnings bar,
// NewAlertBanner, ShiftBanner) overlap with no stack coordination. Fix adds a
// `stacked` prop to NewAlertBanner/ShiftBanner so app.jsx can push them below
// the full-width dataWarnings bar when it is present.
module.exports = [
  {
    name: 'NEW-UX-008 NewAlertBanner stacked=true swaps top-16 for top-24',
    target: 'components.jsx',
    deps: ['icons.jsx'],
    body: `
      ReactDOM.flushSync(() => root.render(React.createElement(window.NewAlertBanner, {
        count: 3, onClick: () => {}, stacked: true,
      })));
      await settle();
      const btn = container.querySelector('button.new-alert-banner');
      A('NEW-UX-008 setup: NewAlertBanner button renders (stacked=true)', !!btn, container.innerHTML.slice(0, 200));
      A('NEW-UX-008 stacked=true button className contains top-24', !!btn && btn.className.indexOf('top-24') !== -1, btn && btn.className);
      A('NEW-UX-008 stacked=true button className does NOT contain top-16', !!btn && btn.className.indexOf('top-16') === -1, btn && btn.className);

      ReactDOM.flushSync(() => root.render(React.createElement(window.NewAlertBanner, {
        count: 3, onClick: () => {}, stacked: false,
      })));
      await settle();
      const btn2 = container.querySelector('button.new-alert-banner');
      A('NEW-UX-008 setup: NewAlertBanner button renders (stacked=false)', !!btn2, container.innerHTML.slice(0, 200));
      A('NEW-UX-008 stacked=false button className contains top-16', !!btn2 && btn2.className.indexOf('top-16') !== -1, btn2 && btn2.className);
      A('NEW-UX-008 stacked=false button className does NOT contain top-24', !!btn2 && btn2.className.indexOf('top-24') === -1, btn2 && btn2.className);
    `,
  },
  {
    name: 'NEW-UX-008 ShiftBanner stacked=true swaps top-14 for top-24',
    target: 'components.jsx',
    deps: ['icons.jsx'],
    body: `
      ReactDOM.flushSync(() => root.render(React.createElement(window.ShiftBanner, {
        shiftSummary: {}, onDismiss: () => {}, onViewHandover: () => {}, stacked: true,
      })));
      await settle();
      const wrap = container.querySelector('div.fixed');
      A('NEW-UX-008 setup: ShiftBanner wrapper renders (stacked=true)', !!wrap, container.innerHTML.slice(0, 200));
      A('NEW-UX-008 stacked=true wrapper className contains top-24', !!wrap && wrap.className.indexOf('top-24') !== -1, wrap && wrap.className);
      A('NEW-UX-008 stacked=true wrapper className does NOT contain top-14', !!wrap && wrap.className.indexOf('top-14') === -1, wrap && wrap.className);

      ReactDOM.flushSync(() => root.render(React.createElement(window.ShiftBanner, {
        shiftSummary: {}, onDismiss: () => {}, onViewHandover: () => {}, stacked: false,
      })));
      await settle();
      const wrap2 = container.querySelector('div.fixed');
      A('NEW-UX-008 setup: ShiftBanner wrapper renders (stacked=false)', !!wrap2, container.innerHTML.slice(0, 200));
      A('NEW-UX-008 stacked=false wrapper className contains top-14', !!wrap2 && wrap2.className.indexOf('top-14') !== -1, wrap2 && wrap2.className);
      A('NEW-UX-008 stacked=false wrapper className does NOT contain top-24', !!wrap2 && wrap2.className.indexOf('top-24') === -1, wrap2 && wrap2.className);
    `,
  },
];
