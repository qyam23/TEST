"""Main window layout skeleton.

Step 4b: layout-only scaffold with no pipeline integration.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt


class MainWindow(QMainWindow):
    """Top-level UI shell with placeholder panels for future wiring."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Codex Hardened Pipeline")
        self.resize(1400, 900)

        root = QWidget(self)
        root_layout = QVBoxLayout(root)

        vertical_split = QSplitter(Qt.Orientation.Vertical)
        top_split = QSplitter(Qt.Orientation.Horizontal)

        top_split.addWidget(self._build_file_tree_panel())
        top_split.addWidget(self._build_dag_placeholder_panel())
        top_split.addWidget(self._build_artifacts_panel())
        top_split.setStretchFactor(0, 2)
        top_split.setStretchFactor(1, 5)
        top_split.setStretchFactor(2, 3)

        vertical_split.addWidget(top_split)
        vertical_split.addWidget(self._build_log_console_panel())
        vertical_split.setStretchFactor(0, 7)
        vertical_split.setStretchFactor(1, 3)

        root_layout.addWidget(vertical_split)
        self.setCentralWidget(root)

    def _build_file_tree_panel(self) -> QGroupBox:
        panel = QGroupBox("Files / Clusters")
        layout = QVBoxLayout(panel)

        self.file_tree = QTreeWidget(panel)
        self.file_tree.setHeaderLabels(["Path"])

        cluster_a = QTreeWidgetItem(["cluster A"])
        cluster_a.addChildren([QTreeWidgetItem(["file1.py"]), QTreeWidgetItem(["file2.py"])])
        cluster_b = QTreeWidgetItem(["cluster B"])
        cluster_b.addChild(QTreeWidgetItem(["file3.py"]))

        self.file_tree.addTopLevelItems([cluster_a, cluster_b])
        self.file_tree.expandAll()

        layout.addWidget(self.file_tree)
        return panel

    def _build_dag_placeholder_panel(self) -> QGroupBox:
        panel = QGroupBox("Pipeline DAG (Live)")
        layout = QHBoxLayout(panel)

        self.dag_placeholder = QLabel("DAG visualization placeholder", panel)
        self.dag_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dag_placeholder.setStyleSheet(
            "border: 1px dashed #888; padding: 12px; color: #555;"
        )

        layout.addWidget(self.dag_placeholder)
        return panel

    def _build_artifacts_panel(self) -> QGroupBox:
        panel = QGroupBox("Artifacts")
        layout = QVBoxLayout(panel)

        self.artifact_tree = QTreeWidget(panel)
        self.artifact_tree.setHeaderLabels(["Artifact", "Path"])
        self.artifact_tree.addTopLevelItems(
            [
                QTreeWidgetItem(["original.py", "-"]),
                QTreeWidgetItem(["rewritten.py", "-"]),
                QTreeWidgetItem(["diff.patch", "-"]),
                QTreeWidgetItem(["report.json", "-"]),
                QTreeWidgetItem(["explain.jsonl", "-"]),
            ]
        )

        layout.addWidget(self.artifact_tree)
        return panel

    def _build_log_console_panel(self) -> QGroupBox:
        panel = QGroupBox("Log Console")
        layout = QVBoxLayout(panel)

        self.log_console = QPlainTextEdit(panel)
        self.log_console.setReadOnly(True)
        self.log_console.setPlaceholderText(
            "[INFO] stage=ANALYZED job=abc3 tokens=1842 model=gpt-5"
        )

        layout.addWidget(self.log_console)
        return panel
