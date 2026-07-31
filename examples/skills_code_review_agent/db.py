import datetime
from typing import Any, List, Optional
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

Base = declarative_base()

class ReviewTask(Base):
    __tablename__ = 'review_tasks'
    
    id = Column(String(64), primary_key=True)
    status = Column(String(32), default='PENDING')  # PENDING, IN_PROGRESS, COMPLETED, FAILED, INTERCEPTED
    diff_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))
    
    # Relationships
    sandbox_runs = relationship("SandboxRun", back_populates="task", cascade="all, delete-orphan")
    findings = relationship("Finding", back_populates="task", cascade="all, delete-orphan")
    reports = relationship("ReviewReport", back_populates="task", cascade="all, delete-orphan")
    filter_logs = relationship("FilterLog", back_populates="task", cascade="all, delete-orphan")

class SandboxRun(Base):
    __tablename__ = 'sandbox_runs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), ForeignKey('review_tasks.id'), nullable=False)
    command = Column(Text, nullable=False)
    status = Column(String(32))  # SUCCESS, FAILED, TIMEOUT
    duration_ms = Column(Integer, default=0)
    stdout = Column(Text, nullable=True)
    stderr = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    
    task = relationship("ReviewTask", back_populates="sandbox_runs")

class Finding(Base):
    __tablename__ = 'findings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), ForeignKey('review_tasks.id'), nullable=False)
    severity = Column(String(16))  # critical, high, medium, low
    category = Column(String(64))
    file = Column(Text)
    line = Column(Integer)
    title = Column(Text)
    evidence = Column(Text)
    recommendation = Column(Text)
    confidence = Column(String(16))  # high, medium, low
    source = Column(String(64))      # static_analyzer, llm, rule_engine
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    
    task = relationship("ReviewTask", back_populates="findings")

class ReviewReport(Base):
    __tablename__ = 'review_reports'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), ForeignKey('review_tasks.id'), nullable=False)
    json_content = Column(Text, nullable=False)
    md_content = Column(Text, nullable=False)
    total_duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    
    task = relationship("ReviewTask", back_populates="reports")

class FilterLog(Base):
    __tablename__ = 'filter_logs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), ForeignKey('review_tasks.id'), nullable=False)
    rule_name = Column(String(64))
    action = Column(String(16))  # ALLOW, DENY
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    
    task = relationship("ReviewTask", back_populates="filter_logs")

class ReviewDbRepository:
    """
    Abstrated database repository to support SQLite and other SQL databases.
    """
    def __init__(self, db_url: str = "sqlite:///review_agent.db"):
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def create_task(self, task_id: str, diff_summary: str) -> ReviewTask:
        with self.Session() as session:
            task = ReviewTask(id=task_id, status='PENDING', diff_summary=diff_summary)
            session.add(task)
            session.commit()
            # Refresh to load attributes
            session.refresh(task)
            return task

    def update_task_status(self, task_id: str, status: str):
        with self.Session() as session:
            task = session.query(ReviewTask).filter_by(id=task_id).first()
            if task:
                task.status = status
                session.commit()

    def add_sandbox_run(self, task_id: str, command: str, status: str, duration_ms: int, stdout: str, stderr: str):
        with self.Session() as session:
            run = SandboxRun(
                task_id=task_id,
                command=command,
                status=status,
                duration_ms=duration_ms,
                stdout=stdout,
                stderr=stderr
            )
            session.add(run)
            session.commit()

    def add_findings(self, task_id: str, findings_list: List[dict]):
        with self.Session() as session:
            for f in findings_list:
                finding = Finding(
                    task_id=task_id,
                    severity=f.get("severity", "medium"),
                    category=f.get("category", "Uncategorized"),
                    file=f.get("file"),
                    line=f.get("line"),
                    title=f.get("title"),
                    evidence=f.get("evidence"),
                    recommendation=f.get("recommendation"),
                    confidence=f.get("confidence", "high"),
                    source=f.get("source", "static_analyzer")
                )
                session.add(finding)
            session.commit()

    def add_report(self, task_id: str, json_content: str, md_content: str, total_duration_ms: int):
        with self.Session() as session:
            report = ReviewReport(
                task_id=task_id,
                json_content=json_content,
                md_content=md_content,
                total_duration_ms=total_duration_ms
            )
            session.add(report)
            session.commit()

    def add_filter_log(self, task_id: str, rule_name: str, action: str, reason: str):
        with self.Session() as session:
            log = FilterLog(
                task_id=task_id,
                rule_name=rule_name,
                action=action,
                reason=reason
            )
            session.add(log)
            session.commit()

    def get_task_details(self, task_id: str) -> Optional[dict]:
        with self.Session() as session:
            task = session.query(ReviewTask).filter_by(id=task_id).first()
            if not task:
                return None
            
            # Serialize for easy representation/assertions
            return {
                "id": task.id,
                "status": task.status,
                "diff_summary": task.diff_summary,
                "created_at": task.created_at.isoformat(),
                "sandbox_runs": [
                    {
                        "command": r.command,
                        "status": r.status,
                        "duration_ms": r.duration_ms,
                        "stdout": r.stdout,
                        "stderr": r.stderr
                    } for r in task.sandbox_runs
                ],
                "findings": [
                    {
                        "severity": f.severity,
                        "category": f.category,
                        "file": f.file,
                        "line": f.line,
                        "title": f.title,
                        "evidence": f.evidence,
                        "recommendation": f.recommendation,
                        "confidence": f.confidence,
                        "source": f.source
                    } for f in task.findings
                ],
                "filter_logs": [
                    {
                        "rule_name": l.rule_name,
                        "action": l.action,
                        "reason": l.reason
                    } for l in task.filter_logs
                ],
                "reports": [
                    {
                        "json_content": rep.json_content,
                        "md_content": rep.md_content,
                        "total_duration_ms": rep.total_duration_ms
                    } for rep in task.reports
                ]
            }
