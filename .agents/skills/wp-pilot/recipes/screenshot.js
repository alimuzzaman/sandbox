/**
 * Recipe: screenshot any URL (frontend or admin).
 *
 *   node screenshot.js [--url <url>] [--out <path>] [--admin] [--w 1280] [--h 900] [--full]
 *
 * Defaults to frontend mode. Pass --admin to login first.
 */

const { runInEditor, runOnFrontend } = require('../lib/runner.js');

function parseArgs() {
    const args = { full: false, admin: false, w: 1280, h: 900 };
    const a = process.argv.slice(2);
    for (let i = 0; i < a.length; i++) {
        if (a[i] === '--url') args.url = a[++i];
        else if (a[i] === '--out') args.out = a[++i];
        else if (a[i] === '--admin') args.admin = true;
        else if (a[i] === '--w') args.w = parseInt(a[++i], 10);
        else if (a[i] === '--h') args.h = parseInt(a[++i], 10);
        else if (a[i] === '--full') args.full = true;
    }
    return args;
}

(async () => {
    const args = parseArgs();
    if (!args.url) { console.error('--url <url> required'); process.exit(1); }
    if (!args.out) args.out = `/tmp/wp-pilot-shot-${Date.now()}.png`;

    const common = {
        url: args.url,
        viewport: { width: args.w, height: args.h },
        screenshot: args.out,
        fullPage: args.full,
        settleMs: 2000,
    };

    if (args.admin) {
        await runInEditor({ ...common, evaluate: () => null });
    } else {
        await runOnFrontend({ ...common });
    }
    console.log(args.out);
})();
