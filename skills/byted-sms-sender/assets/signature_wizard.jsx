/* global React, ReactDOM, arco */
// 构建：skills/tests/node_modules/.bin/esbuild skills/specs/skills/byted-sms-sender/assets/signature_wizard.jsx --bundle --minify --outfile=skills/specs/skills/byted-sms-sender/assets/signature_wizard.js
const { Alert, Button, Input, Message, Modal, Select, Spin, Upload } = arco;
const { useEffect, useMemo, useRef, useState } = React;
const token = window.location.hash.slice(1);
if (token) {
  try {
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  } catch (_) {
    // 展示宿主可能限制导航。
  }
}

const sources = [[1, '公司全称／简称'], [2, 'APP 全称'], [3, '商标全称']];
const purposes = [
  [1, '自用', '签名归属于当前火山账号对应的企业'],
  [2, '他用', '签名归属于非当前火山账号对应的企业'],
];
const channels = [['CN_OTP', '验证码短信'], ['CN_NTC', '通知短信'], ['CN_MKT', '营销短信']];
const reviewLabels = { 1: '审核中', 2: '审核未通过', 3: '审核通过', 5: '免审', 16: '等待资质审核通过' };
const materialTypes = {
  other: {
    title: '更多资料',
    hint: '其他有助于审核的证明文件（选填）',
    accept: '.png,.jpg,.jpeg,.doc,.docx,.gif,.zip',
    description: '支持 png、jpg、jpeg、doc、docx、gif、zip 文件',
  },
  app: {
    title: 'APP ICP 备案截图',
    hint: '备案号和附件至少填写一项',
    accept: '.zip,.pdf,.jpg,.png,.jpeg',
    description: '支持 zip、pdf、jpg、png、jpeg 文件',
  },
  trademark: {
    title: '商标证书文件',
    hint: '为提升审核效率，建议上传商标文件或截图',
    accept: '.zip,.pdf,.jpg,.png,.jpeg',
    description: '支持 zip、pdf、jpg、png、jpeg 文件',
  },
};

function fileFromUploadItem(item) {
  return item && (item.originFile || item);
}

function validateSignatureContent(content) {
  if (!content) return '请输入签名内容';
  if (content.trim() !== content) return '签名首尾不能包含空格';
  if (content.length > 25) return '签名最多支持25个字符';
  if (content.length < 2) return '签名最少2个字符';
  if (!/^[\u4E00-\uFA29\uE7C7-\uE7F3a-zA-Z0-9()（）:：\-.'·+&\s]{2,25}$/.test(content)) {
    return '签名存在不支持的特殊符号';
  }
  if (/http:|https:|www\.|\.com/.test(content)) return '签名不能包含网址';
  if (/^\d+$/.test(content)) return '签名不支持纯数字';
  if (/1[3-9]\d{9}|0\d{2,3}-\d{5,8}/.test(content)) {
    return '签名不能包含联系方式';
  }
  if (!/[\u4E00-\uFA29\uE7C7-\uE7F3a-zA-Z0-9]/.test(content)) {
    return '签名至少存在一个中文或英文或数字';
  }
  return '';
}

async function api(path, body, { raw = false, headers = {} } = {}) {
  if (!token) throw new Error('请从会话中的签名申请入口打开本机页面。');
  const options = { headers: { 'X-Signature-Context': token, ...headers } };
  if (body !== undefined) {
    options.method = 'POST';
    options.body = raw ? body : JSON.stringify(body);
    if (!raw) options.headers['Content-Type'] = 'application/json';
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (_) {
    throw new Error('本机页面暂时无法连接。');
  }
  const payload = await response.json();
  if (!response.ok || payload.success === false) {
    const error = new Error(payload.message || '当前操作未完成，请核对输入。');
    error.status = response.status;
    error.result = payload.result;
    error.state = payload.state;
    error.code = payload.code;
    error.requestId = payload.requestId;
    throw error;
  }
  return payload;
}

function App() {
  const [metadata, setMetadata] = useState(null);
  const [values, setValues] = useState({});
  const [files, setFiles] = useState([]);
  const [groupScope, setGroupScope] = useState(null);
  const [step, setStep] = useState('base');
  const [baseCompleted, setBaseCompleted] = useState(false);
  const [preview, setPreview] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});
  const locked = useRef(false);
  const finished = useRef(false);
  const active = useRef(Date.now());

  useEffect(() => {
    let mounted = true;
    api('/api/state').then(({ state }) => {
      if (!mounted) return;
      const initial = {
        ...state.initialData,
        channelTypes: channels.map(([value]) => value),
        from: 'vconsole',
      };
      const qualification = state.qualifications.find(
        row => row.id === initial.signatureIdentificationID,
      );
      if (qualification) initial.purpose = qualification.purpose;
      setMetadata(state);
      setValues(initial);
      setGroupScope(
        initial.isLimitSubAccount === 0
          ? 'all'
          : initial.isLimitSubAccount === 1
            ? 'limited'
            : Array.isArray(initial.subAccounts)
              ? initial.subAccounts.length ? 'limited' : 'all'
              : null,
      );
    }).catch(err => mounted && setError(err.message));
    const heartbeat = () => {
      if (finished.current) return;
      api('/api/heartbeat', { active: Date.now() - active.current < 15000 }).then(() => {
        window.parent.postMessage({ type: 'signature:wizard-ready' }, '*');
      }).catch(() => {});
    };
    heartbeat();
    const timer = setInterval(heartbeat, 5000);
    const detach = () => {
      const headers = new Headers({ 'Content-Type': 'application/json' });
      headers.set('X-Signature-Context', token);
      fetch('/api/detach', {
        method: 'POST',
        keepalive: true,
        headers,
        body: '{}',
      }).catch(() => {});
    };
    window.addEventListener('pagehide', detach);
    return () => {
      mounted = false;
      clearInterval(timer);
      window.removeEventListener('pagehide', detach);
    };
  }, []);

  const material = metadata?.qualifications.find(
    row => row.id === values.signatureIdentificationID,
  );
  const qualificationOptions = useMemo(() => {
    if (!metadata) return [];
    return metadata.qualifications
      .filter(row => values.purpose === undefined || row.purpose === values.purpose)
      .map(row => ({
        value: row.id,
        label: `${row.id}【${row.businessCertificateName}】· ${row.purpose === 1 ? '自用' : '他用'}`,
      }));
  }, [metadata, values.purpose]);
  const selectedFiles = files.filter(file => file.kind === 'other'
    || file.kind === 'app' && values.source === 2
    || file.kind === 'trademark' && values.source === 3);

  const edit = (patch, { invalidateBase = false, clearErrors = Object.keys(patch) } = {}) => {
    active.current = Date.now();
    setValues(previous => ({ ...previous, ...patch }));
    setFieldErrors(previous => Object.fromEntries(
      Object.entries(previous).filter(([field]) => !clearErrors.includes(field)),
    ));
    if (invalidateBase) setBaseCompleted(false);
    setPreview(null);
    setError('');
  };
  const run = async task => {
    if (locked.current || finished.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    active.current = Date.now();
    try {
      await task();
    } catch (err) {
      setError(`${err.message}${err.code ? `（${err.code}）` : ''}${err.requestId ? `，排障编号：${err.requestId}` : ''}`);
    } finally {
      locked.current = false;
      setBusy(false);
    }
  };
  const finish = value => {
    finished.current = true;
    setResult(value);
    window.parent.postMessage({ type: 'signature:wizard-finished' }, '*');
  };
  const changePurpose = purpose => {
    if (metadata.qualificationLocked) return;
    const selected = metadata.qualifications.find(
      row => row.id === values.signatureIdentificationID,
    );
    edit({
      purpose,
      ...(selected?.purpose === purpose ? {} : { signatureIdentificationID: undefined }),
    }, { invalidateBase: true });
  };
  const selectQualification = id => {
    if (id === undefined || id === null || id === '') {
      edit({ signatureIdentificationID: undefined });
      return;
    }
    const next = metadata.qualifications.find(row => row.id === id);
    const useFullName = !values.content
      || values.content === material?.businessCertificateName;
    edit({
      signatureIdentificationID: id,
      purpose: next.purpose,
      ...(values.source === 1 && useFullName
        ? { content: next.businessCertificateName }
        : {}),
    });
  };
  const changeContent = content => {
    edit({
      content,
      ...(!metadata.qualificationLocked && values.purpose === 2
        ? { signatureIdentificationID: undefined }
        : {}),
    }, { invalidateBase: true });
  };
  const nextStep = () => {
    const nextErrors = {};
    if (!values.source) nextErrors.source = '请选择签名类型';
    if (!values.purpose) nextErrors.purpose = '请选择签名归属类型';
    const contentError = validateSignatureContent(values.content);
    if (contentError) nextErrors.content = contentError;
    if (!groupScope) nextErrors.groupScope = '请选择是否限定可使用消息组';
    if (groupScope === 'limited'
        && (!Array.isArray(values.subAccounts) || values.subAccounts.length === 0)) {
      nextErrors.subAccounts = '请选择消息组';
    }
    if (Object.keys(nextErrors).length > 0) {
      setFieldErrors(nextErrors);
      setError('请修正表单中的错误后继续。');
      window.setTimeout(() => {
        const firstField = document.querySelector(
          `[data-validation-field="${Object.keys(nextErrors)[0]}"]`,
        );
        if (firstField?.scrollIntoView) {
          firstField.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        const control = firstField?.querySelector('input, textarea, [tabindex]');
        if (control?.focus) control.focus();
      }, 0);
      return;
    }
    setFieldErrors({});
    setError('');
    setBaseCompleted(true);
    setStep('materials');
  };
  const buildPreview = () => run(async () => {
    const params = {
      ...values,
      from: 'vconsole',
      isLimitSubAccount: groupScope === 'limited' ? 1 : 0,
      ...(material ? { purpose: material.purpose } : {}),
    };
    if (params.source !== 2) delete params.appIcp;
    if (params.source !== 3) delete params.trademark;
    const payload = await api('/api/preview', {
      params,
      fileIds: selectedFiles.map(file => file.id),
    });
    setPreview(payload.preview);
  });
  const submit = acceptRisk => run(async () => {
    try {
      const payload = await api('/api/create', {
        revision: preview.revision,
        confirmed: true,
        acceptRisk,
      });
      finish(payload.result);
    } catch (err) {
      if (err.result || !err.status) {
        finish(err.result || {
          status: 'signature_submission_outcome_unknown',
          outcomeUnknown: true,
        });
      } else {
        if (err.state) {
          setMetadata(err.state);
          setValues(previous => {
            const next = { ...previous };
            const qualification = err.state.qualifications.find(
              row => row.id === previous.signatureIdentificationID,
            );
            if (qualification) next.purpose = qualification.purpose;
            return next;
          });
        }
        setPreview(null);
        throw err;
      }
    }
  });
  const upload = (kind, uploadItem, onSuccess, onError) => {
    const file = fileFromUploadItem(uploadItem);
    run(async () => {
      if (!file) throw new Error('请选择文件。');
      if (files.length >= metadata.maxFiles) {
        throw new Error(`本次最多上传 ${metadata.maxFiles} 份材料。`);
      }
      const payload = await api('/api/file', file, {
        raw: true,
        headers: {
          'X-Material-Kind': kind,
          'X-File-Name': encodeURIComponent(file.name),
        },
      });
      setFiles(previous => [...previous, payload.file]);
      setPreview(null);
      if (onSuccess) onSuccess(payload.file);
    }).catch(error => {
      if (onError) onError(error);
    });
    return { abort() {} };
  };
  const fileField = kind => (
    <section className="signature-field signature-upload" data-material={kind} key={kind}>
      <label>{materialTypes[kind].title}</label>
      <p className="signature-help">{materialTypes[kind].hint}</p>
      <Upload
        className="signature-upload__control"
        accept={materialTypes[kind].accept}
        autoUpload
        disabled={busy || files.length >= metadata.maxFiles}
        drag
        fileList={files.filter(file => file.kind === kind).map(file => ({
          uid: file.id,
          name: file.name,
          status: 'done',
        }))}
        limit={{ maxCount: metadata.maxFiles, hideOnExceedLimit: false }}
        multiple
        customRequest={({ file, onSuccess, onError }) => (
          upload(kind, file, onSuccess, onError)
        )}
        onRemove={item => {
          run(async () => {
            await api('/api/file/remove', { id: item.uid });
            setFiles(previous => previous.filter(row => row.id !== item.uid));
            setPreview(null);
          });
          return false;
        }}
        onExceedLimit={() => Message.warning(
          `本次最多上传 ${metadata.maxFiles} 份材料`,
        )}
      >
        <div className="signature-upload__trigger">
          <span className="signature-upload__icon">＋</span>
          <strong>点击或拖拽文件到这里</strong>
          <small>{materialTypes[kind].description}</small>
        </div>
      </Upload>
    </section>
  );

  if (result) {
    return (
      <main className="signature-shell">
        <section className="signature-page signature-result" data-state="completed">
          <div className={`signature-result__icon${result.outcomeUnknown ? ' is-warning' : ''}`}>
            {result.outcomeUnknown ? '!' : '✓'}
          </div>
          <h1>
            {result.outcomeUnknown
              ? '申请结果待核实'
              : result.status === 'signature_submitted'
                ? '签名申请已提交'
                : '本次填写已结束'}
          </h1>
          <p>
            {result.outcomeUnknown
              ? '请先通过申请记录和排障编号查询结果，避免重复提交。'
              : result.status === 'signature_submitted'
                ? '已取得申请编号，审核与报备结果请继续查询。'
                : '已清理本机草稿和材料。'}
          </p>
          {result.applyId && <div className="signature-result__detail">
            <span>申请编号</span><strong>{String(result.applyId)}</strong>
          </div>}
          {result.applyId && <div className="signature-result__detail">
            <span>本次请求返回状态</span>
            <strong>{reviewLabels[result.reviewStatus] || '待查询'}</strong>
          </div>}
          {result.requestId && <div className="signature-result__detail">
            <span>排障编号</span><strong>{result.requestId}</strong>
          </div>}
        </section>
      </main>
    );
  }
  if (!metadata) {
    return <main className="signature-shell">
      <section className="signature-page signature-loading">
        {error ? <Alert type="error" content={error} /> : <Spin tip="正在读取已有资质" />}
      </section>
    </main>;
  }

  return (
    <main className="signature-shell" onInput={() => { active.current = Date.now(); }}>
      <section className="signature-page">
        <header className="signature-header">
          <div>
            <span className="signature-eyebrow">国内短信</span>
            <h1>创建短信签名</h1>
            <p>签名将显示在短信正文开头，请按实际使用主体填写并提交审核。</p>
          </div>
          <a
            href="https://www.volcengine.com/docs/6361/1551554"
            target="_blank"
            rel="noreferrer"
          >
            查看签名规范
          </a>
        </header>

        <nav className="signature-steps" aria-label="申请步骤">
          <button
            type="button"
            className={`signature-step is-active${step === 'base' ? ' is-current' : ''}`}
            onClick={() => {
              setPreview(null);
              setStep('base');
            }}
          >
            <span>{baseCompleted ? '✓' : '1'}</span>
            <strong>签名信息</strong>
          </button>
          <i />
          <button
            type="button"
            className={`signature-step${step === 'materials' ? ' is-active is-current' : ''}`}
            onClick={() => {
              if (baseCompleted) setStep('materials');
            }}
          >
            <span>2</span>
            <strong>资质材料</strong>
          </button>
        </nav>

        {error && <Alert type="error" content={error} />}

        {step === 'base' ? (
          <form onSubmit={event => {
            event.preventDefault();
            nextStep();
          }}>
            <section className="signature-panel">
              <div className="signature-panel__title">
                <span>01</span>
                <div><h2>签名信息</h2><p>填写签名展示方式和适用范围</p></div>
              </div>
              <div className="signature-grid">
                <section
                  className={`signature-field${fieldErrors.source ? ' is-error' : ''}`}
                  data-validation-field="source"
                >
                  <label>签名类型 <em>*</em></label>
                  <Select
                    aria-label="签名类型"
                    value={values.source}
                    disabled={busy}
                    placeholder="请选择签名类型"
                    options={sources.map(([value, label]) => ({ value, label }))}
                    onChange={source => edit({ source }, { invalidateBase: true })}
                  />
                  {fieldErrors.source && <p className="signature-field-error">{fieldErrors.source}</p>}
                </section>
                <section
                  className={`signature-field${fieldErrors.purpose ? ' is-error' : ''}`}
                  data-validation-field="purpose"
                >
                  <label>签名归属类型 <em>*</em></label>
                  <Select
                    aria-label="签名归属类型"
                    value={values.purpose}
                    disabled={busy || metadata.qualificationLocked}
                    placeholder="请选择签名归属类型"
                    options={purposes.map(([value, label]) => ({ value, label }))}
                    onChange={changePurpose}
                  />
                  {fieldErrors.purpose && <p className="signature-field-error">{fieldErrors.purpose}</p>}
                  <p className="signature-help">
                    {purposes.find(([value]) => value === values.purpose)?.[2]
                      || '根据签名实际归属企业选择'}
                  </p>
                </section>
              </div>
              <section
                className={`signature-field${fieldErrors.content ? ' is-error' : ''}`}
                data-validation-field="content"
              >
                <label htmlFor="signature-name">签名内容 <em>*</em></label>
                <Input
                  id="signature-name"
                  name="content"
                  value={values.content || ''}
                  disabled={busy}
                  maxLength={25}
                  showWordLimit
                  onChange={changeContent}
                  placeholder="请输入签名内容"
                />
                {fieldErrors.content && <p className="signature-field-error">{fieldErrors.content}</p>}
                <p className="signature-help">
                  请输入 2–25 个字符，建议使用可核验的公司、APP 或商标名称。
                </p>
              </section>
              <section
                className={`signature-field${fieldErrors.groupScope ? ' is-error' : ''}`}
                data-validation-field="groupScope"
              >
                <label>发送类型 <em>*</em></label>
                <div className="signature-static-options">
                  {channels.map(([, label]) => <span key={label}>✓ {label}</span>)}
                </div>
                <p className="signature-help">签名审核通过后可用于国内短信的全部发送类型。</p>
              </section>
              <section className="signature-field">
                <label>是否限定可使用消息组 <em>*</em></label>
                <div className="signature-choice-group">
                  <label
                    className={groupScope === 'limited' ? 'is-selected' : ''}
                  >
                    <input
                      type="radio"
                      name="group-scope"
                      checked={groupScope === 'limited'}
                      onChange={() => {
                        setGroupScope('limited');
                        edit({
                          isLimitSubAccount: 1,
                          subAccounts: values.subAccounts?.length
                            ? values.subAccounts
                            : undefined,
                        }, {
                          invalidateBase: true,
                          clearErrors: ['groupScope', 'subAccounts'],
                        });
                      }}
                    />
                    <span><strong>是</strong><small>仅指定消息组可使用</small></span>
                  </label>
                  <label
                    className={groupScope === 'all' ? 'is-selected' : ''}
                  >
                    <input
                      type="radio"
                      name="group-scope"
                      checked={groupScope === 'all'}
                      onChange={() => {
                        setGroupScope('all');
                        edit({
                          isLimitSubAccount: 0,
                          subAccounts: [],
                        }, {
                          invalidateBase: true,
                          clearErrors: ['groupScope', 'subAccounts'],
                        });
                      }}
                    />
                    <span><strong>否</strong><small>全部消息组均可使用</small></span>
                  </label>
                </div>
                {fieldErrors.groupScope
                  && <p className="signature-field-error">{fieldErrors.groupScope}</p>}
              </section>
              {groupScope === 'limited' && (
                <section
                  className={`signature-field${fieldErrors.subAccounts ? ' is-error' : ''}`}
                  data-validation-field="subAccounts"
                >
                  <label>允许使用的消息组范围 <em>*</em></label>
                  <Select
                    aria-label="允许使用的消息组范围"
                    mode="multiple"
                    value={values.subAccounts || []}
                    disabled={busy}
                    placeholder="请选择消息组"
                    options={metadata.messageGroups.map(row => ({
                      label: row.name,
                      value: row.id,
                    }))}
                    onChange={subAccounts => edit(
                      { subAccounts: subAccounts.length ? subAccounts : undefined },
                      { invalidateBase: true },
                    )}
                  />
                  {fieldErrors.subAccounts
                    && <p className="signature-field-error">{fieldErrors.subAccounts}</p>}
                </section>
              )}
              <section className="signature-field">
                <label htmlFor="signature-description">申请说明</label>
                <Input.TextArea
                  id="signature-description"
                  name="desc"
                  value={values.desc || ''}
                  maxLength={300}
                  showWordLimit
                  disabled={busy}
                  onChange={desc => edit({ desc }, { invalidateBase: true })}
                  placeholder="请输入该签名的使用场景"
                />
              </section>
            </section>
            <div className="signature-actions signature-actions--split">
              <Button
                disabled={busy}
                onClick={() => run(async () => finish(
                  (await api('/api/abandon', {})).result,
                ))}
              >
                放弃本次填写
              </Button>
              <Button type="primary" htmlType="submit" disabled={busy}>下一步</Button>
            </div>
          </form>
        ) : (
          <form onSubmit={event => {
            event.preventDefault();
            buildPreview();
          }}>
            <section className="signature-panel">
              <div className="signature-panel__title">
                <span>02</span>
                <div><h2>资质材料</h2><p>选择企业资质并按签名类型补充证明</p></div>
              </div>

              {values.source === 2 && <div className="signature-subsection">
                <div className="signature-subsection__title">
                  <div><h3>APP ICP 备案信息</h3><p>备案号和附件至少填写一项</p></div>
                  <a
                    href="https://www.volcengine.com/docs/6361/1551554"
                    target="_blank"
                    rel="noreferrer"
                  >
                    填写说明
                  </a>
                </div>
                <section className="signature-field">
                  <label htmlFor="app-number">APP ICP 备案号</label>
                  <Input
                    id="app-number"
                    name="appIcpFilling"
                    value={values.appIcp?.appIcpFilling || ''}
                    disabled={busy}
                    onChange={appIcpFilling => edit({
                      appIcp: { ...values.appIcp, appIcpFilling },
                    })}
                    placeholder="请输入 APP ICP 备案号"
                  />
                </section>
                {fileField('app')}
              </div>}

              {values.source === 3 && <div className="signature-subsection">
                <div className="signature-subsection__title">
                  <div><h3>商标信息</h3><p>商标注册号为必填项</p></div>
                  <a
                    href="https://www.volcengine.com/docs/6361/1551554"
                    target="_blank"
                    rel="noreferrer"
                  >
                    填写说明
                  </a>
                </div>
                <div className="signature-grid">
                  <section className="signature-field">
                    <label htmlFor="trademark-cn">商标中文名</label>
                    <Input
                      id="trademark-cn"
                      name="trademarkCn"
                      value={values.trademark?.trademarkCn || ''}
                      disabled={busy}
                      onChange={trademarkCn => edit({
                        trademark: { ...values.trademark, trademarkCn },
                      })}
                      placeholder="请输入商标中文名"
                    />
                  </section>
                  <section className="signature-field">
                    <label htmlFor="trademark-en">商标英文名</label>
                    <Input
                      id="trademark-en"
                      name="trademarkEn"
                      value={values.trademark?.trademarkEn || ''}
                      disabled={busy}
                      onChange={trademarkEn => edit({
                        trademark: { ...values.trademark, trademarkEn },
                      })}
                      placeholder="请输入商标英文名"
                    />
                  </section>
                </div>
                <section className="signature-field">
                  <label htmlFor="trademark-number">商标注册号 <em>*</em></label>
                  <Input
                    id="trademark-number"
                    name="trademarkNumber"
                    value={values.trademark?.trademarkNumber || ''}
                    disabled={busy}
                    onChange={trademarkNumber => edit({
                      trademark: { ...values.trademark, trademarkNumber },
                    })}
                    placeholder="请输入商标注册号"
                  />
                </section>
                {fileField('trademark')}
              </div>}

              <section className="signature-field">
                <label>资质 <em>*</em></label>
                <Select
                  aria-label="资质"
                  value={values.signatureIdentificationID}
                  disabled={busy || metadata.qualificationLocked}
                  allowClear={!metadata.qualificationLocked}
                  placeholder={values.purpose
                    ? '请选择与归属类型一致的资质'
                    : '请先选择签名归属类型'}
                  options={qualificationOptions}
                  onChange={selectQualification}
                />
                {metadata.qualificationLocked
                  ? <p className="signature-help">该资质由申请上下文确定，本次不可更换。</p>
                  : <p className="signature-help">
                    仅展示当前账号下审核通过且与归属类型一致的可用资质。
                  </p>}
              </section>
              {material && <div className="signature-qualification" data-field="qualification">
                <div>
                  <span>资质 ID</span><strong>{material.id}</strong>
                </div>
                <div>
                  <span>企业名称</span><strong>{material.businessCertificateName}</strong>
                </div>
                <div>
                  <span>资质归属</span><strong>{material.purpose === 1 ? '自用' : '他用'}</strong>
                </div>
                <div>
                  <span>资质状态</span><strong className="is-success">审核通过，可用</strong>
                </div>
              </div>}

              {fileField('other')}

              {preview && <section className="signature-preview" aria-label="申请预览">
                <div className="signature-preview__title">
                  <div>
                    <span>✓</span>
                    <div><h2>请核对本次申请</h2><p>确认无误后提交审核</p></div>
                  </div>
                  <Button size="small" disabled={busy} onClick={() => setPreview(null)}>
                    返回修改
                  </Button>
                </div>
                <dl className="signature-summary">
                  <dt>签名内容</dt><dd>{preview.content}</dd>
                  <dt>签名类型</dt>
                  <dd>{sources.find(([value]) => value === preview.source)?.[1]}</dd>
                  <dt>签名归属</dt><dd>{preview.purpose === 1 ? '自用' : '他用'}</dd>
                  <dt>企业资质</dt>
                  <dd>{preview.signatureIdentificationID}【{preview.businessCertificateName}】</dd>
                  <dt>发送类型</dt>
                  <dd>{preview.channelTypes.map(
                    value => channels.find(([key]) => key === value)?.[1],
                  ).join('、')}</dd>
                  <dt>消息组</dt>
                  <dd>{preview.subAccounts.length
                    ? preview.subAccounts.map(
                      id => metadata.messageGroups.find(row => row.id === id)?.name,
                    ).join('、')
                    : '全部消息组'}</dd>
                  <dt>补充材料</dt>
                  <dd>{preview.materials.length
                    ? preview.materials.map(file => file.name).join('、')
                    : '未添加'}</dd>
                </dl>
                {preview.warnings.map(warning => (
                  <Alert key={warning.code} type="warning" content={warning.message} />
                ))}
                {preview.warnings.length > 0 && <p className="signature-help">
                  当前签名可能存在报备失败风险，建议返回修改；继续提交后仍需审核。
                </p>}
              </section>}
            </section>

            <div className="signature-actions signature-actions--split">
              <div>
                <Button
                  disabled={busy}
                  onClick={() => {
                    setPreview(null);
                    setStep('base');
                  }}
                >
                  上一步
                </Button>
                <Button
                  disabled={busy}
                  onClick={() => run(async () => finish(
                    (await api('/api/abandon', {})).result,
                  ))}
                >
                  放弃本次填写
                </Button>
              </div>
              {!preview
                ? <Button type="primary" htmlType="submit" disabled={busy}>核对申请</Button>
                : preview.warnings.length
                  ? <Button
                    type="primary"
                    status="warning"
                    disabled={busy}
                    onClick={() => Modal.confirm({
                      title: '确认承担签名报备风险',
                      content: '当前签名未连续出现在营业执照企业名称中，可能存在审核或报备失败风险。是否仍要强制提交？',
                      okText: '确认承担风险并提交',
                      cancelText: '返回修改',
                      onOk: () => submit(true),
                    })}
                  >
                    强制提交
                  </Button>
                  : <Button
                    type="primary"
                    disabled={busy}
                    onClick={() => submit(false)}
                  >
                    确认提交
                  </Button>}
            </div>
          </form>
        )}
      </section>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById('signature-root')).render(<App />);
