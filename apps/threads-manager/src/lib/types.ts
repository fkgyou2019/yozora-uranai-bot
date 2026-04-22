export interface ThreadsAccount {
  id: string;
  name: string;
  username: string;
  persona: string;
  group: string;
  enabled: boolean;
  auth: {
    user_id: string;
    access_token: string;
    token_expires_at: string;
  };
  otp_url: string;
  limits: {
    max_posts_per_day: number;
    min_interval_seconds: number;
  };
}

export interface Group {
  id: string;
  description: string;
}

export interface AccountsFile {
  x_accounts?: any[];
  threads_accounts: ThreadsAccount[];
  groups: Group[];
  personas?: string[];
}
