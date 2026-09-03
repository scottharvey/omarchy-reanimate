import QtQuick
import Quickshell
import Quickshell.Io

// Headless service: replay the saved session once when the shell starts.
// The restore takes an exclusive lock and consumes its manifest, so a shell
// restart or a parallel post-boot hook run is a safe no-op.
Item {
  id: root

  readonly property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "")

  Process {
    id: restoreProc
    command: [root.pluginDir + "bin/omarchy-reanimate-restore"]
  }

  Component.onCompleted: restoreProc.running = true
}
