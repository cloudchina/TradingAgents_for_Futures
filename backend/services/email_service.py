"""邮件发送服务 - 供定时分析的 auto_email 自动发送分析报告

配置为普通配置项，写入 backend/.env 即可（也可用系统环境变量覆盖）：
  SMTP_HOST      SMTP 服务器地址，如 smtp.qq.com
  SMTP_PORT      端口，默认 465（SSL）；若为 587/25 则自动使用 STARTTLS
  SMTP_USER      发件账号
  SMTP_PASSWORD  授权码/密码
  SMTP_FROM      发件人显示地址（可选，默认同 SMTP_USER）
  SMTP_TO        收件人，多个用逗号/分号分隔
"""
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import formataddr, formatdate
from pathlib import Path
from typing import Optional, List
from loguru import logger

from core.settings import settings


class EmailService:
    """基于 smtplib 的邮件发送服务（配置来自 backend/.env 的 settings，非系统环境变量）"""

    def __init__(self):
        self.host: str = (settings.SMTP_HOST or "").strip()
        try:
            self.port: int = int(settings.SMTP_PORT or 465)
        except (TypeError, ValueError):
            self.port = 465
        self.user: str = (settings.SMTP_USER or "").strip()
        self.password: str = (settings.SMTP_PASSWORD or "").strip()
        self._from: str = (settings.SMTP_FROM or "").strip() or self.user
        to_raw = (settings.SMTP_TO or "").strip()
        self.to_list: List[str] = [
            x.strip() for x in to_raw.replace(";", ",").split(",") if x.strip()
        ]
        self.available: bool = bool(self.host and self.user and self.password and self.to_list)
        if not self.available:
            logger.warning(
                "SMTP_* 未完整配置 (HOST/USER/PASSWORD/TO，写入 backend/.env)，auto_email 将不可用"
            )

    def send_analysis_report(
        self,
        subject: str,
        body: str,
        attachment_path: Optional[Path] = None,
        attachment_filename: Optional[str] = None,
    ) -> bool:
        """发送分析报告邮件。

        Args:
            subject: 邮件主题
            body: 正文纯文本
            attachment_path: 附件文件（Word 报告），可选
            attachment_filename: 附件展示文件名，可选（默认取 attachment_path 文件名）
        """
        if not self.available:
            logger.error("邮件服务未配置，发送失败")
            return False

        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = formataddr(("期货AI自动分析", self._from))
        msg["To"] = ", ".join(self.to_list)
        msg["Date"] = formatdate(localtime=True)
        msg.attach(MIMEText(body, "plain", "utf-8"))

        if attachment_path is not None:
            path = Path(attachment_path)
            if not path.exists():
                logger.error(f"邮件附件不存在: {path}")
                return False
            with open(path, "rb") as f:
                part = MIMEApplication(f.read())
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=("utf-8", "", attachment_filename or path.name),
            )
            msg.attach(part)

        try:
            if self.port == 465:
                # SSL 直连
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=30) as server:
                    server.login(self.user, self.password)
                    server.sendmail(self._from, self.to_list, msg.as_string())
            else:
                # 默认端口/SMTP：先明文建连再升级 STARTTLS（如服务器支持）
                with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                    server.ehlo()
                    try:
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                    except smtplib.SMTPException:
                        pass
                    server.login(self.user, self.password)
                    server.sendmail(self._from, self.to_list, msg.as_string())

            logger.info(f"邮件已发送: 主题='{subject}', 收件人={self.to_list}")
            return True

        except Exception as e:
            logger.error(f"发送邮件失败: {e}")
            return False


# 全局单例
email_service = EmailService()
