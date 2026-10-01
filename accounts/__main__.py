import os
import uvicorn
from .config import from_environment
from .main import create_app

uvicorn.run(create_app(from_environment()), host='0.0.0.0', port=int(os.environ.get('PORT', '8000')),
            proxy_headers=True, forwarded_allow_ips=os.environ.get('FORWARDED_ALLOW_IPS', ''),
            access_log=False)
