# Homebrew formula for the Sandbox CLI.
#
# Ship this from a tap repo (e.g. templately/homebrew-sandbox as Formula/sandbox.rb):
#     brew tap templately/sandbox
#     brew install sandbox
# Or install bleeding-edge straight from git:
#     brew install --HEAD templately/sandbox/sandbox
#
# Release flow: scripts/make-release.sh builds dist/sandbox-<ver>.tar.gz; upload it
# to the BASE_URL (sandbox.xc1.app), then set `url`/`sha256` below to that exact
# artifact (sha256: `shasum -a 256 dist/sandbox-<ver>.tar.gz`).
class Sandbox < Formula
  desc "MCP-first, per-project WordPress dev/test stack — cd into a plugin, boot + test"
  homepage "https://sandbox.xc1.app/"
  url "https://sandbox.xc1.app/sandbox-0.1.0.tar.gz"
  sha256 "REPLACE_WITH_RELEASE_TARBALL_SHA256"
  license "GPL-2.0-or-later"
  head "https://github.com/templately/sandbox.git", branch: "main"

  depends_on "python@3.12"

  def install
    # The release tarball unpacks to a top-level sandbox/ dir; a --HEAD clone is flat.
    src = (buildpath/"sandbox").directory? ? buildpath/"sandbox" : buildpath
    libexec.install Dir["#{src}/*"]
    # Wrap `sb` so the bundled Homebrew python3 is on PATH (sb self-bootstraps a
    # venv for PyYAML on first run, then re-execs through it).
    env = { PATH: "#{Formula["python@3.12"].opt_bin}:$PATH" }
    (bin/"sandbox").write_env_script libexec/"sb", env
    (bin/"sb").write_env_script libexec/"sb", env
  end

  def caveats
    <<~EOS
      sandbox drives Docker. Install and start Docker Desktop, OrbStack, or another
      Docker-compatible engine before using the stack:
        sandbox setup                       # one-time: build MCP venv, wire Claude
        cd your-plugin && sandbox init      # scaffold config, boot an instance
        sandbox test                        # run the plugin's phpunit tests
    EOS
  end

  test do
    assert_match "usage: sandbox", shell_output("#{bin}/sandbox --help")
  end
end
