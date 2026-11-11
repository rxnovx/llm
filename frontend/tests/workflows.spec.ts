import {expect,test} from '@playwright/test';

test('질문 분기·답변 버전 비교·실제 파일 다운로드와 프로젝트 모달',async({page},info)=>{
  await page.goto('/user/signup');
  const username=`workflows_${info.project.name}_${Date.now()}`;
  await page.getByLabel('표시 이름',{exact:true}).fill('워크플로 사용자');
  await page.getByLabel('사용자명',{exact:true}).fill(username);
  await page.getByLabel('이메일',{exact:true}).fill(`${username}@example.com`);
  await page.getByLabel('비밀번호',{exact:true}).fill('WorkflowsBrowserPass123!');
  await page.getByLabel('비밀번호 확인',{exact:true}).fill('WorkflowsBrowserPass123!');
  await page.getByRole('checkbox').check();await page.getByRole('button',{name:'회원가입',exact:true}).click();
  await expect(page.getByRole('heading',{name:'무엇을 함께 해볼까요?'})).toBeVisible();
  await page.getByRole('button',{name:'프로젝트 관리',exact:true}).click();
  const project=page.getByRole('dialog',{name:'대화·자료·지침을 한곳에'});
  await project.getByLabel('프로젝트 이름',{exact:true}).fill('검증 프로젝트');
  await project.getByLabel('이 프로젝트의 응답 지침').fill('한국어로 답변');
  await project.getByRole('button',{name:'프로젝트 만들기',exact:true}).click();
  await project.getByRole('button',{name:'프로젝트 대화 보기',exact:true}).click();
  await page.getByLabel('메시지',{exact:true}).fill('검증 대화의 첫 질문');
  await page.getByRole('button',{name:'메시지 전송',exact:true}).click();
  await expect(page.getByRole('button',{name:'답변 복사',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'다시 생성',exact:true}).click();
  await expect(page.getByRole('button',{name:'답변 버전 2개',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'답변 버전 2개',exact:true}).click();
  const versions=page.getByRole('dialog',{name:'답변 버전 비교'});
  await expect(versions.locator('.cw-version-body')).toHaveCount(2);
  await versions.getByRole('button',{name:'닫기',exact:true}).click();
  await page.getByText('답변을 파일로 저장',{exact:true}).click();
  await page.getByLabel('저장 형식',{exact:true}).selectOption('pdf');
  await page.getByRole('button',{name:'파일 만들기',exact:true}).click();
  const file=page.locator('.cw-result-files li');await expect(file).toContainText('대화 결과.pdf');
  await file.getByRole('button',{name:/대화 결과.pdf/}).click();
  const preview=page.getByRole('dialog',{name:'결과 파일 미리보기'});await expect(preview).toContainText('대화 결과.pdf');
  const downloading=page.waitForEvent('download');await preview.getByRole('button',{name:'다운로드',exact:true}).click();expect((await downloading).suggestedFilename()).toBe('대화 결과.pdf');
  await preview.getByRole('button',{name:'닫기',exact:true}).click();
  const before=page.url();await page.getByRole('button',{name:'질문 수정',exact:true}).click();
  const edit=page.getByRole('dialog',{name:'질문 수정'});await edit.getByLabel('수정할 질문').fill('수정한 질문');
  await edit.getByRole('button',{name:'수정한 대화 열기',exact:true}).click();
  await expect(page.getByLabel('메시지',{exact:true})).toHaveValue('수정한 질문');expect(page.url()).not.toBe(before);
});

test('붙여넣거나 놓은 파일을 미리 보고 전송할 때 첨부한다',async({page},info)=>{
  await page.goto('/user/signup');const username=`uploads_${info.project.name}_${Date.now()}`;
  for(const [label,value] of [['표시 이름','첨부 사용자'],['사용자명',username],['이메일',`${username}@example.com`],['비밀번호','WorkflowsBrowserPass123!'],['비밀번호 확인','WorkflowsBrowserPass123!']])await page.getByLabel(label,{exact:true}).fill(value);
  await page.getByRole('checkbox').check();await page.getByRole('button',{name:'회원가입',exact:true}).click();
  await expect(page.getByRole('heading',{name:'무엇을 함께 해볼까요?'})).toBeVisible();
  const requests: string[] = [];
  let sending = false;
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (!sending && request.method() === 'POST' &&
      /^\/api\/(chat\/conversations|workflows\/uploads|knowledge\/documents)(\/|$)/.test(path)) requests.push(path);
  });

  const previews = page.getByRole('list', {name:'첨부 미리보기'});
  const editor = page.getByLabel('메시지', {exact:true});
  const imagePasted = await editor.evaluate(async element => {
    const canvas = document.createElement('canvas');
    canvas.width = 12;
    canvas.height = 12;
    const image = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!image) throw new Error('테스트 이미지를 만들지 못했습니다.');
    const transfer = new DataTransfer();
    transfer.items.add(new File([image], 'clipboard.png', {type:'image/png'}));
    const paste = new ClipboardEvent('paste', {clipboardData:transfer, bubbles:true, cancelable:true});
    element.dispatchEvent(paste);
    return paste.defaultPrevented;
  });
  expect(imagePasted).toBe(true);
  const thumbnail = previews.getByRole('img', {name:'clipboard.png'});
  await expect(thumbnail).toBeVisible();
  await expect(thumbnail).toHaveJSProperty('naturalWidth', 12);
  await previews.getByRole('button', {name:'clipboard.png 제거'}).click();
  await expect(previews).toHaveCount(0);

  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button',{name:'첨부',exact:true}).click();
  await (await chooser).setFiles({name:'one.md',mimeType:'text/markdown',buffer:Buffer.from('첫 번째 문서입니다.')});
  const dropped = await page.evaluateHandle(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(['두 번째 문서입니다.'], 'two.md', {type:'text/markdown'}));
    transfer.items.add(new File(['제거할 문서입니다.'], 'remove.md', {type:'text/markdown'}));
    return transfer;
  });
  await page.locator('.cw-main').dispatchEvent('drop', {dataTransfer:dropped});
  await dropped.dispose();
  await expect(previews.getByRole('listitem')).toHaveCount(3);
  await previews.getByRole('button', {name:'remove.md 제거'}).click();
  await expect(previews.getByRole('listitem')).toHaveCount(2);
  await expect(previews).toContainText('one.md');
  await expect(previews).toContainText('two.md');
  await editor.fill('본문에서 찾을 고유 문구');
  expect(requests).toEqual([]);
  expect(new URL(page.url()).searchParams.has('chat')).toBe(false);

  const turn = page.waitForRequest(request => request.method() === 'POST' &&
    /^\/api\/chat\/conversations\/[^/]+\/messages$/.test(new URL(request.url()).pathname), {timeout:30000});
  sending = true;
  await page.getByRole('button',{name:'메시지 전송',exact:true}).click();
  const payload = (await turn).postDataJSON();
  expect(payload.assistant.document_ids).toHaveLength(2);
  await expect(page.getByRole('button',{name:'답변 복사',exact:true})).toBeVisible();
  await expect(previews).toHaveCount(0);
  const sidebar = page.getByRole('button', {name:'사이드바 열기'});
  if (await sidebar.isVisible()) await sidebar.click();
  await page.getByRole('button',{name:'대화 찾기'}).click();await page.getByLabel('대화 제목·본문 검색').fill('고유 문구');
  await expect(page.locator('.cw-search-excerpt')).toContainText('고유 문구');
});
