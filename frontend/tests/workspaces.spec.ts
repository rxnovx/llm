import {expect,test} from '@playwright/test';

test('프로젝트 분석 선택과 작업공간 모달이 작성 중인 질문을 유지한다',async({page},info)=>{
  const connection='77777777-7777-4777-8777-777777777777';
  const workspace='88888888-8888-4888-8888-888888888888';
  await page.route('**/api/chat/config',async route=>{
    const response=await route.fetch();const data=await response.json();
    await route.fulfill({json:{...data,terminal:{enabled:true,execution_enabled:true}}});
  });
  await page.route('**/api/apps/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith('/catalog'))return route.fulfill({json:{apps:[{id:'gitlab',name:'GitLab',providers:[{id:'office',name:'사내 GitLab',base_url:'https://gitlab.example',ready:true}],connections:[{id:connection,provider_id:'office',username:'alice',status:'connected'}]}]}});
    if(path.endsWith('/branches'))return route.fulfill({json:{items:[{name:'main'}],has_more:false}});
    return route.fulfill({json:{items:[{id:7,name:'team/service',default_branch:'main'}],has_more:false}});
  });
  await page.route('**/api/workspaces**',async route=>{
    if(route.request().method()==='GET')return route.fulfill({json:{items:[{id:workspace,conversation_id:'current',status:'ready',allow_execution:false,ref:'main',project_id:7,source:{project:'team/service',commit:'a'.repeat(40)}}]}});
    const body=route.request().postDataJSON();
    return route.fulfill({json:body.name==='terminal_jobs'?{items:[]}:{files:[{path:'main.py',bytes:40}],total_files:1}});
  });
  await page.goto('/user/signup');
  const username=`workspace_${info.project.name}_${Date.now()}`;
  await page.getByLabel('표시 이름',{exact:true}).fill('작업공간 사용자');
  await page.getByLabel('사용자명',{exact:true}).fill(username);
  await page.getByLabel('이메일',{exact:true}).fill(`${username}@example.com`);
  await page.getByLabel('비밀번호',{exact:true}).fill('WorkspaceBrowserPass123!');
  await page.getByLabel('비밀번호 확인',{exact:true}).fill('WorkspaceBrowserPass123!');
  await page.getByRole('checkbox').check();await page.getByRole('button',{name:'회원가입',exact:true}).click();
  await expect(page.getByRole('heading',{name:'무엇을 함께 해볼까요?'})).toBeVisible();
  await page.getByLabel('메시지',{exact:true}).fill('프로젝트 분석');
  await page.getByRole('button',{name:'앱 추가',exact:true}).click();
  await page.getByRole('option',{name:'GitLab 범위 직접 선택',exact:true}).click();
  const scope=page.getByRole('dialog',{name:'GitLab에서 가져오기'});
  await scope.getByLabel('작업 방식').selectOption('workspace');
  await scope.getByRole('button',{name:/team\/service/}).click();
  await scope.getByRole('button',{name:'이 질문에 추가',exact:true}).click();
  await expect(page.locator('.cw-app-selection')).toContainText('프로젝트 분석');
  await page.route('**/api/chat/conversations/*/messages',async route=>{
    const input=route.request().postDataJSON();expect(input.apps[0].mode).toBe('workspace');
    const conversation={id:new URL(route.request().url()).pathname.split('/').at(-2),connection_id:'demo',model:'demo',title:'프로젝트 분석',busy:false};
    const human={id:'human',role:'user',content:'프로젝트 분석',status:'complete'};
    const assistant={id:'answer',role:'assistant',content:'분석했습니다.',status:'complete',apps:[{...input.apps[0],workspace_id:workspace}],tools:[{name:'repo_map',step:1,status:'complete',detail:'파일 1개'}]};
    const event=(name:string,data:unknown)=>`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
    await route.fulfill({contentType:'text/event-stream',body:event('start',{conversation,user_message:human,assistant_message:{...assistant,content:'',status:'pending'}})
      +event('workspace',{...conversation,id:workspace,project_id:7,status:'ready'})+event('activity',assistant.tools[0])+event('delta',{content:'분석했습니다.'})+event('done',{conversation,message:assistant})});
  });
  await page.getByRole('button',{name:'메시지 전송',exact:true}).click();
  await expect(page.getByRole('button',{name:'답변 복사',exact:true})).toBeVisible();
  await page.getByLabel('메시지',{exact:true}).fill('다음 질문 유지');
  await page.getByRole('button',{name:'작업공간',exact:true}).click();
  const modal=page.getByRole('dialog',{name:'프로젝트 작업공간'});
  await modal.getByRole('button',{name:'저장소 지도',exact:true}).click();
  await expect(modal.getByLabel('도구 조회 결과')).toContainText('main.py');
  await modal.getByRole('button',{name:'작업공간 닫기',exact:true}).click();
  await expect(page.getByLabel('메시지',{exact:true})).toHaveValue('다음 질문 유지');
  await expect(page.locator('.cw-app-selection')).toContainText('프로젝트 분석');
});
