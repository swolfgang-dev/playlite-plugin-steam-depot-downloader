"""Native Qt presentation for the Steam setup flow."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QWidget,QFrame,QLabel,QVBoxLayout,QHBoxLayout,QProgressBar,QScrollArea,QLayout,QSizePolicy


def style_dialog(dialog):
    palette=dialog.palette()
    accent=palette.color(QPalette.ColorRole.Highlight).name()
    surface=palette.color(QPalette.ColorRole.AlternateBase).name()
    muted=palette.color(QPalette.ColorRole.PlaceholderText).name()
    dialog.setObjectName('steamSetup')
    dialog.setStyleSheet(f'''
        #steamSetup QLabel#setupTitle {{ font-size: 24px; font-weight: 600; }}
        #steamSetup QLabel#setupSubtitle {{ color: {muted}; }}
        #steamSetup QFrame#setupCard {{ background: {surface}; border-radius: 12px; }}
        #steamSetup QLabel#setupStepTitle {{ font-size: 19px; font-weight: 600; }}
        #steamSetup QLabel#setupStep {{ padding: 8px 10px; border-radius: 6px; color: {muted}; }}
        #steamSetup QLabel#setupStep[current="true"] {{ color: {accent}; font-weight: 600; background: {surface}; }}
        #steamSetup QLabel#setupStep[completed="true"] {{ color: {accent}; }}
        #steamSetup QPushButton {{ padding: 9px 16px; min-height: 22px; }}
        #steamSetup QPushButton#setupPrimary {{ background: {accent}; color: {palette.color(QPalette.ColorRole.HighlightedText).name()}; font-weight: 600; border: none; border-radius: 6px; }}
        #steamSetup QPushButton#setupPrimary:disabled {{ background: {surface}; color: {muted}; }}
        #steamSetup QLineEdit {{ padding: 10px; min-height: 22px; }}
        #steamSetup QPlainTextEdit {{ font-family: monospace; font-size: 12px; border-radius: 6px; }}
        #steamSetup QProgressBar#setupOverall {{ border: none; background: {surface}; height: 4px; }}
        #steamSetup QProgressBar#setupOverall::chunk {{ background: {accent}; }}
        #steamSetup QLabel#setupFeedback {{ padding: 10px; border-radius: 6px; color: {muted}; }}
        #steamSetup QLabel#setupFeedback[error="true"] {{ color: #e99488; }}
    ''')


def build(dialog, steps):
    old=dialog.layout()
    intro=old.itemAt(0).widget()
    if intro:intro.hide()
    # Keep actions outside the scrollable form. The form may grow beyond
    # the viewport when fonts are large or diagnostic logs are expanded.
    actions=None
    for index in range(old.count()):
        child=old.itemAt(index).layout()
        if child is not None and child.indexOf(dialog.buttons[0])>=0:
            actions=old.takeAt(index).layout();break
    old.removeWidget(dialog.close_button)
    content=QWidget();content.setLayout(old)
    content.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.MinimumExpanding)
    old.setContentsMargins(0,0,0,0);old.setSpacing(18)
    old.setAlignment(Qt.AlignmentFlag.AlignTop)
    old.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    for label in content.findChildren(QLabel):
        policy=label.sizePolicy();policy.setHorizontalPolicy(QSizePolicy.Policy.Expanding);label.setSizePolicy(policy)
    dialog.details_toggle.setFlat(True)
    old.setAlignment(dialog.details_toggle,Qt.AlignmentFlag.AlignLeft)
    dialog.close_button.setFlat(True)
    card=QFrame(dialog);card.setObjectName('setupCard')
    card_layout=QVBoxLayout(card);card_layout.setContentsMargins(24,24,24,20);card_layout.setSpacing(18)
    scroll=QScrollArea();scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    scroll.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }')
    card_layout.addWidget(scroll,1)
    if actions is not None:
        actions.setSpacing(12);card_layout.addLayout(actions)
    card_layout.addWidget(dialog.close_button,0,Qt.AlignmentFlag.AlignRight)
    dialog.form_scroll=scroll
    dialog.setup_card=card
    root=QVBoxLayout(dialog);root.setContentsMargins(24,24,24,24);root.setSpacing(16)
    title=QLabel('Set up Steam Downloader');title.setObjectName('setupTitle');root.addWidget(title)
    subtitle=QLabel('A private Steam environment. Your games stay in the folder you choose.')
    subtitle.setObjectName('setupSubtitle');subtitle.setWordWrap(True);root.addWidget(subtitle)
    overall=QProgressBar();overall.setObjectName('setupOverall');overall.setRange(0,len(steps));overall.setTextVisible(False);overall.setFixedHeight(4);root.addWidget(overall)
    row=QHBoxLayout();row.setSpacing(24);root.addLayout(row,1)
    sidebar=QWidget();side=QVBoxLayout(sidebar);side.setContentsMargins(0,8,0,0);side.setSpacing(3)
    labels=[]
    short=('Configure','Create VM & VPN','Install Steam','Steam sign-in','LuaTools & Hubcap')
    for index,text in enumerate(short):
        label=QLabel(f'{index+1:02}  {text}');label.setObjectName('setupStep');side.addWidget(label);labels.append(label)
    side.addStretch();sidebar.setMinimumWidth(200);row.addWidget(sidebar);row.addWidget(card,1)
    dialog.stage.setObjectName('setupStepTitle');dialog.stage.setWordWrap(True)
    dialog.status.setObjectName('setupFeedback');dialog.status.setTextFormat(Qt.TextFormat.PlainText)
    dialog.buttons[0].setObjectName('setupPrimary')
    dialog.close_button.setText('Close setup')
    dialog.log.setFixedHeight(180)
    dialog.resize(940,660)
    dialog.setMinimumSize(800,560)
    style_dialog(dialog)
    def update(step):
        overall.setValue(step)
        for index,label in enumerate(labels):
            label.setText(('✓' if index<step else f'{index+1:02}')+'  '+short[index])
            label.setProperty('current',index==step);label.setProperty('completed',index<step)
            label.style().unpolish(label);label.style().polish(label)
    def feedback(error=False):
        dialog.status.setProperty('error',error)
        dialog.status.style().unpolish(dialog.status);dialog.status.style().polish(dialog.status)
        dialog.status.setToolTip(dialog.status.text())
        if error:dialog.status.setText(dialog.status.text().split('\n',1)[0]+' Open the setup log for details.')
    dialog.visual_step_labels=labels
    dialog.visual_progress=overall
    dialog.update_step_visuals=update
    dialog.update_feedback_visuals=feedback
    update(dialog.wizard_step)
