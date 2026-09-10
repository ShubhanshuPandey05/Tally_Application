#include <flutter/dart_project.h>
#include <flutter/flutter_view_controller.h>
#include <flutter_windows.h>
#include <windows.h>

#include "flutter_window.h"
#include "utils.h"

namespace {

// The window's title, and half of how a second launch finds the first one.
constexpr wchar_t kWindowTitle[] = L"TallyFlow Connector";

// Registered by Win32Window for every Flutter runner window.
constexpr wchar_t kWindowClass[] = L"FLUTTER_RUNNER_WIN32_WINDOW";

// Held for the lifetime of the process. Its only job is to answer "is one
// already running?" before a second window is drawn.
constexpr wchar_t kInstanceMutex[] = L"Local\\TallyFlowConnectorWindow";

// Raises the window an earlier launch already opened, if there is one.
//
// Without this, clicking the Start menu entry twice leaves two windows polling
// the same connector and disagreeing with each other by a poll interval, which
// looks exactly like the connector flapping.
bool RaiseExistingWindow() {
  HWND existing = ::FindWindowW(kWindowClass, kWindowTitle);
  if (existing == nullptr) {
    return false;
  }
  if (::IsIconic(existing)) {
    ::ShowWindow(existing, SW_RESTORE);
  }
  ::SetForegroundWindow(existing);
  return true;
}

// Where to put the window: centred on the monitor holding the cursor, in the
// logical pixels Win32Window::Create expects.
//
// The default runner opens at (10, 10), which on a shop's single 1366x768
// screen puts a 900-pixel-tall window's buttons under the taskbar.
Win32Window::Point CentredOrigin(const Win32Window::Size& size) {
  POINT cursor{};
  ::GetCursorPos(&cursor);
  HMONITOR monitor = ::MonitorFromPoint(cursor, MONITOR_DEFAULTTOPRIMARY);

  MONITORINFO info{};
  info.cbSize = sizeof(info);
  if (::GetMonitorInfoW(monitor, &info) == 0) {
    return Win32Window::Point(10, 10);
  }

  UINT dpi = FlutterDesktopGetDpiForMonitor(monitor);
  double scale = dpi > 0 ? dpi / 96.0 : 1.0;
  double width = (info.rcWork.right - info.rcWork.left) / scale;
  double height = (info.rcWork.bottom - info.rcWork.top) / scale;
  double left = info.rcWork.left / scale;
  double top = info.rcWork.top / scale;

  // Clamped at the work area's own corner: on a screen smaller than the window
  // a negative origin would push the title bar off the top, where it cannot be
  // dragged back.
  unsigned int x = static_cast<unsigned int>(
      left + (width > size.width ? (width - size.width) / 2 : 0));
  unsigned int y = static_cast<unsigned int>(
      top + (height > size.height ? (height - size.height) / 2 : 0));
  return Win32Window::Point(x, y);
}

}  // namespace

int APIENTRY wWinMain(_In_ HINSTANCE instance, _In_opt_ HINSTANCE prev,
                      _In_ wchar_t *command_line, _In_ int show_command) {
  // Taken before anything else. A second copy of this window is never wanted --
  // see RaiseExistingWindow -- and the mutex is released with the process, so a
  // crash cannot leave the machine unable to open it again.
  HANDLE instance_lock = ::CreateMutexW(nullptr, TRUE, kInstanceMutex);
  if (instance_lock != nullptr && ::GetLastError() == ERROR_ALREADY_EXISTS) {
    RaiseExistingWindow();
    return EXIT_SUCCESS;
  }

  // Attach to console when present (e.g., 'flutter run') or create a
  // new console when running with a debugger.
  if (!::AttachConsole(ATTACH_PARENT_PROCESS) && ::IsDebuggerPresent()) {
    CreateAndAttachConsole();
  }

  // Initialize COM, so that it is available for use in the library and/or
  // plugins.
  ::CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

  flutter::DartProject project(L"data");

  std::vector<std::string> command_line_arguments =
      GetCommandLineArguments();

  project.set_dart_entrypoint_arguments(std::move(command_line_arguments));

  FlutterWindow window(project);
  // Tall rather than wide: this window is a column of short rows -- companies,
  // then people -- and a 1280-wide default would set a shop's four company
  // names across a metre of desk.
  Win32Window::Size size(940, 760);
  if (!window.Create(kWindowTitle, CentredOrigin(size), size)) {
    return EXIT_FAILURE;
  }
  window.SetQuitOnClose(true);

  ::MSG msg;
  while (::GetMessage(&msg, nullptr, 0, 0)) {
    ::TranslateMessage(&msg);
    ::DispatchMessage(&msg);
  }

  ::CoUninitialize();
  if (instance_lock != nullptr) {
    ::CloseHandle(instance_lock);
  }
  return EXIT_SUCCESS;
}
