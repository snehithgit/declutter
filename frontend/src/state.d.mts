export type Page = 'overview' | 'duplicates' | 'classification' | 'review' | 'export';
export type Route = {page: Page; detail: string | null};
export type Scope = {readonly filters: Readonly<Record<string,string|number|boolean>>;readonly total:number;readonly digest:string;readonly key:string};
export function parseRoute(hash:string):Route;
export function routeHash(page:Page,detail?:string|null):string;
export function createLatestGate():{next():{signal:AbortSignal;isCurrent():boolean};cancel():void};
export function fetchJson<T=unknown>(url:string,init?:RequestInit):Promise<T>;
export function makeScope(filters:Record<string,string|number|boolean>,total:number,digest:string):Scope;
export function sameScope(scope:Scope|null,filters:Record<string,string|number|boolean>):boolean;
