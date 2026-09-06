// Veritas Launcher — Shared entry point
//
// main() delegates to platformMain() which is implemented per-platform:
//   main_windows.go — Windows Service Control Manager
//   main_linux.go   — systemd via systemctl
//   main_darwin.go  — launchd via launchctl
//
// fatalf() is also shared here since all platforms use it.

package main

import (
	"fmt"
	"os"
)

func main() {
	platformMain()
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "ERROR: "+format+"\n", args...)
	os.Exit(1)
}
